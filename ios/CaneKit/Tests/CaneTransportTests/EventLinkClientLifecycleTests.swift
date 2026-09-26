import CaneContracts
import Foundation
import XCTest

@testable import CaneTransport

/// Covers the two lifecycle findings from review round 1 (#2, #3):
/// dropping the last reference without `disconnect()` must still
/// deallocate the client, and `disconnect()` then `connect()` must resume
/// yielding on the *same* `events` stream rather than a dead one.
final class EventLinkClientLifecycleTests: XCTestCase {
    /// No server: `connect()` will keep retrying (fast failures, capped
    /// backoff) forever unless the client itself is collectible.
    private static let nothingListeningURL = URL(string: "ws://127.0.0.1:1")!

    func testClientDeallocatesAfterLastReferenceDroppedWithoutDisconnect() async throws {
        weak var weakClient: EventLinkClient?
        do {
            let client = EventLinkClient(url: Self.nothingListeningURL)
            weakClient = client
            client.connect()
        }
        // The only strong reference above was the `let client` inside the
        // `do` block, now out of scope. Poll rather than sleep once: the
        // exact moment of deallocation depends on where in the reconnect
        // loop the last reference was dropped (see EventLinkClient's
        // `deinit` doc comment).
        var deallocated = false
        for _ in 0..<20 { // up to ~1 s at 50 ms polls
            if weakClient == nil {
                deallocated = true
                break
            }
            try await Task.sleep(for: .milliseconds(50))
        }
        XCTAssertTrue(
            deallocated,
            "EventLinkClient must deallocate once its last strong reference is dropped, even without disconnect()"
        )
    }

    func testDisconnectThenConnectResumesYieldingOnTheSameEventsStream() async throws {
        guard let uvPath = PythonServerProcess.findExecutable(named: "uv") else {
            throw XCTSkip("'uv' not found on PATH; cannot spawn the Python event link server")
        }
        if let importError = PythonServerProcess.probeCaneImportable(uvPath: uvPath) {
            throw XCTSkip("'import cane' failed in backend/'s venv; cannot spawn the server: \(importError)")
        }

        let port = try PythonServerProcess.findFreePort()
        let process = PythonServerProcess.makeServerProcess(
            uvPath: uvPath, port: port, pingInterval: 0.2, demoEventEvery: 0.2
        )
        try process.run()
        defer {
            process.terminate()
            process.waitUntilExit()
        }
        try await Task.sleep(for: .milliseconds(500))

        let url = URL(string: "ws://127.0.0.1:\(port)")!
        let client = EventLinkClient(url: url)
        defer { client.disconnect() }

        // `AsyncStream`'s buffer/continuation live in the stream's own
        // shared storage, not in any one `AsyncIterator` value, so making
        // a fresh iterator per `withTimeout` call below (rather than
        // sharing one `var` mutated across a `@Sendable` closure boundary)
        // still continues reading the *same* underlying stream.
        @Sendable func nextEvent() async -> AwarenessEvent? {
            var iterator = client.events.makeAsyncIterator()
            return await iterator.next()
        }

        client.connect()
        let first = try await withTimeout(seconds: 15) { await nextEvent() }
        XCTAssertNotNil(first, "expected an event before the first disconnect")

        client.disconnect()
        // Give the reconnect loop a moment to actually tear down the
        // socket before reconnecting, so this exercises a real
        // disconnect/reconnect cycle rather than two overlapping
        // `connect()` calls.
        try await Task.sleep(for: .milliseconds(300))

        client.connect()
        let second = try await withTimeout(seconds: 15) { await nextEvent() }
        XCTAssertNotNil(
            second,
            "the same `events` stream must keep yielding after disconnect() + connect() -- "
                + "disconnect() must not finish it"
        )
    }
}
