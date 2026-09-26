import CaneContracts
import Foundation
import XCTest

@testable import CaneTransport

/// Spawns the real Python `EventLinkServer` (D4's CLI) as a subprocess and
/// drives a real `EventLinkClient` against it over a real socket -- the
/// runbook's "events round-trip end to end" acceptance criterion (D6).
///
/// Skips (does not fail) if this machine cannot run the Python side:
/// `uv` missing from PATH, or `backend/.venv` not yet created by
/// `uv sync`.
final class EventLinkClientEndToEndTests: XCTestCase {
    private static var backendDirectory: URL {
        URL(fileURLWithPath: #filePath)
            .deletingLastPathComponent() // EventLinkClientEndToEndTests.swift -> CaneTransportTests/
            .deletingLastPathComponent() // CaneTransportTests/ -> Tests/
            .deletingLastPathComponent() // Tests/ -> CaneKit/
            .deletingLastPathComponent() // CaneKit/ -> ios/
            .deletingLastPathComponent() // ios/ -> repo root
            .appendingPathComponent("backend")
    }

    private func findExecutable(named name: String) -> String? {
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
    private func findFreePort() throws -> Int {
        let socketFD = Darwin.socket(AF_INET, SOCK_STREAM, 0)
        guard socketFD >= 0 else { throw TestSetupError.couldNotOpenSocket }
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
        guard bindResult == 0 else { throw TestSetupError.couldNotBindSocket }

        var boundAddress = sockaddr_in()
        var boundLength = socklen_t(MemoryLayout<sockaddr_in>.size)
        let nameResult = withUnsafeMutablePointer(to: &boundAddress) { pointer -> Int32 in
            pointer.withMemoryRebound(to: sockaddr.self, capacity: 1) { sockaddrPointer in
                Darwin.getsockname(socketFD, sockaddrPointer, &boundLength)
            }
        }
        guard nameResult == 0 else { throw TestSetupError.couldNotBindSocket }
        return Int(UInt16(bigEndian: boundAddress.sin_port))
    }

    private enum TestSetupError: Error {
        case couldNotOpenSocket
        case couldNotBindSocket
    }

    func testEventRoundTripsEndToEndThroughRealServerAndClient() async throws {
        guard let uvPath = findExecutable(named: "uv") else {
            throw XCTSkip("'uv' not found on PATH; cannot spawn the Python event link server")
        }
        let venvDirectory = Self.backendDirectory.appendingPathComponent(".venv")
        guard FileManager.default.fileExists(atPath: venvDirectory.path) else {
            throw XCTSkip("backend/.venv does not exist; run 'cd backend && uv sync' first")
        }

        let port = try findFreePort()

        let process = Process()
        process.executableURL = URL(fileURLWithPath: uvPath)
        process.arguments = [
            "run", "python", "-m", "cane.transport.server",
            "--port", String(port),
            "--ping-interval", "0.2",
            "--demo-event-every", "0.3",
        ]
        process.currentDirectoryURL = Self.backendDirectory
        // Silence the server's stderr (offset/RTT prints) from the test log;
        // failures are diagnosed via the client-side assertions below.
        process.standardOutput = FileHandle.nullDevice
        process.standardError = FileHandle.nullDevice
        try process.run()
        defer {
            process.terminate()
            process.waitUntilExit()
        }

        // Give the server a moment to bind before the client dials in.
        try await Task.sleep(for: .milliseconds(500))

        let url = URL(string: "ws://127.0.0.1:\(port)")!
        let client = EventLinkClient(url: url)
        client.connect()
        defer { client.disconnect() }

        let receivedEvent = try await withTimeout(seconds: 15) {
            var iterator = client.events.makeAsyncIterator()
            return await iterator.next()
        }

        let event = try XCTUnwrap(receivedEvent, "expected at least one AwarenessEvent from the server")
        XCTAssertEqual(event.source, .phone)
    }

    /// XCTest has no built-in per-assertion timeout for an arbitrary
    /// `async` expression; races the real work against a sleeping
    /// "timeout" task and returns whichever finishes first.
    private func withTimeout<T: Sendable>(
        seconds: Double,
        operation: @escaping @Sendable () async -> T
    ) async throws -> T {
        try await withThrowingTaskGroup(of: RaceResult<T>.self) { group in
            defer { group.cancelAll() }

            group.addTask {
                .value(await operation())
            }
            group.addTask {
                try await Task.sleep(for: .seconds(seconds))
                return .timedOut
            }

            guard let first = try await group.next() else {
                throw TimeoutError()
            }
            switch first {
            case .value(let value):
                return value
            case .timedOut:
                throw TimeoutError()
            }
        }
    }

    private enum RaceResult<T: Sendable>: Sendable {
        case value(T)
        case timedOut
    }

    private struct TimeoutError: Error {}
}
