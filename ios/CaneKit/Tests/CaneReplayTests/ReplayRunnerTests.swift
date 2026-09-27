import CaneContracts
import CaneFeedback
import XCTest

import CaneReplay

/// End-to-end over the real gauntlet: a recorded line in, a tally out.
final class ReplayRunnerTests: XCTestCase {
    /// The PRD's headline case. `near` + closing above 3 m/s with no metric
    /// range is urgency 3, and 0.81 clears the 0.60 Stop floor.
    func testAFastCarInTheNearBandProducesAStop() async throws {
        let result = await ReplayRunner.run(lines: [try Fixture.line(try Fixture.event())])

        XCTAssertEqual(result.tally.stops, 1)
        XCTAssertEqual(result.linesRejected, 0)
    }

    /// Receding is a hard zero: you cannot be hit by something going away.
    func testARecedingCarIsNeverAnnounced() async throws {
        let event = try Fixture.event(closingMps: -2.0)

        let result = await ReplayRunner.run(lines: [try Fixture.line(event)])

        XCTAssertEqual(result.tally.announcements, 0)
    }

    /// The whole reason for record-then-replay: the same input must give the
    /// same answer, or a before/after comparison means nothing.
    func testTheSameRecordingTalliesIdenticallyTwice() async throws {
        let lines = [
            try Fixture.lineAt(offsetMs: 0, trackId: "a"),
            try Fixture.lineAt(offsetMs: 3_000, trackId: "b"),
            try Fixture.lineAt(offsetMs: 6_000, trackId: "c"),
        ]

        let first = await ReplayRunner.run(lines: lines)
        let second = await ReplayRunner.run(lines: lines)

        XCTAssertEqual(first.tally, second.tally)
        XCTAssertEqual(first.tally.announcements, 3)
    }

    /// The contract rejects unknown keys, so the emit stamp has to come off
    /// before the event is decoded. Without the strip this yields an empty
    /// tally and a silently useless baseline.
    func testARecordedLineCarryingItsEmitStampReplaysCleanly() async throws {
        let line = try Fixture.line(try Fixture.event())
        XCTAssertTrue(line.contains("t_emit_ms"), "fixture must carry the stamp")

        let result = await ReplayRunner.run(lines: [line])

        XCTAssertEqual(result.linesRead, 1)
        XCTAssertEqual(result.linesRejected, 0)
        XCTAssertEqual(result.tally.announcements, 1)
    }

    /// A truncated recording -- a killed walk -- must still yield its numbers.
    func testAMalformedLineIsCountedAndTheRestStillReplays() async throws {
        let lines = [
            try Fixture.lineAt(offsetMs: 0, trackId: "a"),
            #"{"schema": 1, "truncated"#,
            try Fixture.lineAt(offsetMs: 3_000, trackId: "b"),
        ]

        let result = await ReplayRunner.run(lines: lines)

        XCTAssertEqual(result.linesRejected, 1)
        XCTAssertEqual(result.tally.announcements, 2)
    }

    func testALineWithoutAnEmitStampIsRejected() async throws {
        let encoded = try JSONEncoder().encode(try Fixture.event())
        let bare = try XCTUnwrap(String(data: encoded, encoding: .utf8))

        let result = await ReplayRunner.run(lines: [bare])

        XCTAssertEqual(result.linesRejected, 1)
        XCTAssertEqual(result.tally.announcements, 0)
    }

    func testBlankLinesAreIgnoredRatherThanRejected() async throws {
        let lines = ["", "   ", try Fixture.line(try Fixture.event())]

        let result = await ReplayRunner.run(lines: lines)

        XCTAssertEqual(result.linesRead, 1)
        XCTAssertEqual(result.linesRejected, 0)
    }

    func testAnEmptyRecordingIsAnEmptyTally() async {
        let result = await ReplayRunner.run(lines: [])

        XCTAssertEqual(result.tally, ReplayTally())
        XCTAssertEqual(result.linesRead, 0)
    }
}
