import CaneContracts
import Foundation
import XCTest

@testable import CaneTransport

/// Spawns the real Python `EventLinkServer` (D4's CLI) as a subprocess and
/// drives a real `EventLinkClient` against it over a real socket -- the
/// runbook's "events round-trip end to end" acceptance criterion (D6).
///
/// Skips (does not fail, and does not silently time out) if this machine
/// cannot run the Python side: `uv` missing from PATH, or `import cane`
/// itself failing (see `PythonServerProcess.probeCaneImportable`).
final class EventLinkClientEndToEndTests: XCTestCase {
    func testEventRoundTripsEndToEndThroughRealServerAndClient() async throws {
        guard let uvPath = PythonServerProcess.findExecutable(named: "uv") else {
            throw XCTSkip("'uv' not found on PATH; cannot spawn the Python event link server")
        }
        // Probes actual importability rather than just checking
        // `backend/.venv` exists: a broken editable install (review
        // finding #6) would otherwise fail this test only after the full
        // 15 s timeout below, instead of skipping loudly right here with
        // the real error.
        if let importError = PythonServerProcess.probeCaneImportable(uvPath: uvPath) {
            throw XCTSkip("'import cane' failed in backend/'s venv; cannot spawn the server: \(importError)")
        }

        let port = try PythonServerProcess.findFreePort()
        let process = PythonServerProcess.makeServerProcess(
            uvPath: uvPath, port: port, pingInterval: 0.2, demoEventEvery: 0.3
        )
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
}
