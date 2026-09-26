import Foundation

/// Shared support for spawning the real Python `cane.transport.server` as
/// a subprocess: finding `uv`, finding a free port, and building the
/// process's environment so `uv run python -m cane.transport.server`
/// works from a fresh `git clone` + `uv sync` with nothing outside the
/// repo required (review finding #6) -- no machine-local `sitecustomize.py`
/// needed.
enum PythonServerProcess {
    static var backendDirectory: URL {
        URL(fileURLWithPath: #filePath)
            .deletingLastPathComponent() // PythonServerProcess.swift -> CaneTransportTests/
            .deletingLastPathComponent() // CaneTransportTests/ -> Tests/
            .deletingLastPathComponent() // Tests/ -> CaneKit/
            .deletingLastPathComponent() // CaneKit/ -> ios/
            .deletingLastPathComponent() // ios/ -> repo root
            .appendingPathComponent("backend")
    }

    static func findExecutable(named name: String) -> String? {
        guard let pathEnvironment = ProcessInfo.processInfo.environment["PATH"] else { return nil }
        for directory in pathEnvironment.split(separator: ":") {
            let candidate = URL(fileURLWithPath: String(directory)).appendingPathComponent(name)
            if FileManager.default.isExecutableFile(atPath: candidate.path) {
                return candidate.path
            }
        }
        return nil
    }

    /// A free TCP port picked by binding to port 0 and immediately
    /// releasing it -- a small, accepted race, but this is a local test.
    static func findFreePort() throws -> Int {
        let socketFD = Darwin.socket(AF_INET, SOCK_STREAM, 0)
        guard socketFD >= 0 else { throw SetupError.couldNotOpenSocket }
        defer { Darwin.close(socketFD) }

        var address = sockaddr_in()
        address.sin_family = sa_family_t(AF_INET)
        address.sin_port = 0
        address.sin_addr.s_addr = INADDR_ANY // host-order 0 == network-order 0
        let bindResult = withUnsafePointer(to: &address) { pointer -> Int32 in
            pointer.withMemoryRebound(to: sockaddr.self, capacity: 1) { sockaddrPointer in
                Darwin.bind(socketFD, sockaddrPointer, socklen_t(MemoryLayout<sockaddr_in>.size))
            }
        }
        guard bindResult == 0 else { throw SetupError.couldNotBindSocket }

        var boundAddress = sockaddr_in()
        var boundLength = socklen_t(MemoryLayout<sockaddr_in>.size)
        let nameResult = withUnsafeMutablePointer(to: &boundAddress) { pointer -> Int32 in
            pointer.withMemoryRebound(to: sockaddr.self, capacity: 1) { sockaddrPointer in
                Darwin.getsockname(socketFD, sockaddrPointer, &boundLength)
            }
        }
        guard nameResult == 0 else { throw SetupError.couldNotBindSocket }
        return Int(UInt16(bigEndian: boundAddress.sin_port))
    }

    /// The environment every spawned/probed `uv run` command uses: the
    /// caller's environment plus `PYTHONPATH=src` (relative to
    /// `backendDirectory`, matching `pyproject.toml`'s
    /// `pythonpath = ["src"]` pytest setting), so `import cane` succeeds
    /// even when `uv sync`'s editable install's `.pth` file is skipped by
    /// `site.py` (a real, reproducible `uv`-on-macOS quirk -- see
    /// `backend/README.md`), without any machine-local workaround.
    static func environment() -> [String: String] {
        var environment = ProcessInfo.processInfo.environment
        environment["PYTHONPATH"] = "src"
        return environment
    }

    /// Runs `uv run python -c "import cane"` (same env/cwd the real server
    /// spawn uses) and returns `nil` on success, or the captured
    /// stderr/stdout on failure -- so a broken environment can be reported
    /// via a loud `XCTSkip`, not a silent 15 s timeout.
    static func probeCaneImportable(uvPath: String) -> String? {
        let probe = Process()
        probe.executableURL = URL(fileURLWithPath: uvPath)
        probe.arguments = ["run", "python", "-c", "import cane"]
        probe.currentDirectoryURL = backendDirectory
        probe.environment = environment()

        let outputPipe = Pipe()
        probe.standardOutput = outputPipe
        probe.standardError = outputPipe

        do {
            try probe.run()
        } catch {
            return "failed to launch 'uv run python -c \"import cane\"': \(error)"
        }
        probe.waitUntilExit()

        guard probe.terminationStatus != 0 else { return nil }
        let output = outputPipe.fileHandleForReading.readDataToEndOfFile()
        let message = String(data: output, encoding: .utf8) ?? "<no output>"
        return "exit code \(probe.terminationStatus): \(message)"
    }

    /// Builds (but does not `run()`) the server process: `uv run python -m
    /// cane.transport.server --port <port> --ping-interval <pingInterval>
    /// --demo-event-every <demoEventEvery>`, cwd `backend/`, with
    /// `PYTHONPATH=src` set (see `environment()`).
    static func makeServerProcess(uvPath: String, port: Int, pingInterval: Double, demoEventEvery: Double) -> Process {
        let process = Process()
        process.executableURL = URL(fileURLWithPath: uvPath)
        process.arguments = [
            "run", "python", "-m", "cane.transport.server",
            "--port", String(port),
            "--ping-interval", String(pingInterval),
            "--demo-event-every", String(demoEventEvery),
        ]
        process.currentDirectoryURL = backendDirectory
        process.environment = environment()
        process.standardOutput = FileHandle.nullDevice
        process.standardError = FileHandle.nullDevice
        return process
    }

    enum SetupError: Error {
        case couldNotOpenSocket
        case couldNotBindSocket
    }
}
