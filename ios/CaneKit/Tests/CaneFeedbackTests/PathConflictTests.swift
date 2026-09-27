import CaneContracts
import XCTest

@testable import CaneFeedback

/// The gate: a Stop is for something on a collision course, not for anything
/// whose distance happens to be shrinking.
///
/// Every test here also checks the blast radius. The cap is **one rung** —
/// urgency 2 still speaks the direction and the class — so even a wrong
/// suppression costs the word "Stop", never the warning.
final class PathConflictTests: XCTestCase {
    private let t0 = Make.t0

    private func car(bearingDeg: Double = 47.0) throws -> AwarenessEvent {
        try Make.event(
            source: .x4Cloud, trackId: "car", tCaptureMs: t0 + 500, cls: .car,
            confidence: 0.81, bearingDeg: bearingDeg, rangeBand: .near, closingMps: 9.5
        )
    }

    /// Two sightings `sweepDeg` apart over half a second.
    private func records(sweepDeg: Double, trackId: String = "car") throws -> [AcceptedRecord] {
        let first = try Make.event(
            source: .x4Cloud, trackId: trackId, tCaptureMs: t0, cls: .car,
            bearingDeg: 0, rangeBand: .near, closingMps: 9.5
        )
        let second = try Make.event(
            source: .x4Cloud, trackId: trackId, tCaptureMs: t0 + 500, cls: .car,
            bearingDeg: sweepDeg, rangeBand: .near, closingMps: 9.5
        )
        return [
            AcceptedRecord(event: first, arrivalMs: t0),
            AcceptedRecord(event: second, arrivalMs: t0 + 500),
        ]
    }

    private let stop = UrgencyDecision(urgency: UrgencyLevel.immediate, driver: .band(.near))

    // MARK: - The thing it is for

    func testAStopForASweepingCarIsCappedToActSoon() throws {
        // 60 degrees in half a second: 120 deg/s, well past anything a torso
        // does. This is the car passing on the next lane.
        let result = PathConflict.capped(
            stop, event: try car(), records: try records(sweepDeg: 60)
        )

        XCTAssertTrue(result.capped)
        XCTAssertEqual(result.decision.urgency, UrgencyLevel.actSoon)
    }

    func testASweepToTheLeftIsCappedToo() throws {
        let result = PathConflict.capped(
            stop, event: try car(bearingDeg: -47), records: try records(sweepDeg: -60)
        )

        XCTAssertTrue(result.capped)
        XCTAssertEqual(result.decision.urgency, UrgencyLevel.actSoon)
    }

    /// The case the gate must never break: something actually coming at you
    /// holds its bearing.
    func testAHeadOnApproachKeepsItsStop() throws {
        let result = PathConflict.capped(
            stop, event: try car(bearingDeg: 1), records: try records(sweepDeg: 1)
        )

        XCTAssertFalse(result.capped)
        XCTAssertEqual(result.decision, stop)
    }

    func testTheCapNeverGoesBelowActSoon() throws {
        let result = PathConflict.capped(
            stop, event: try car(), records: try records(sweepDeg: 170)
        )

        XCTAssertEqual(result.decision.urgency, UrgencyLevel.actSoon)
    }

    func testACappedDecisionKeepsItsDriverAndHardZeroFlag() throws {
        let decision = UrgencyDecision(
            urgency: UrgencyLevel.immediate, driver: .ttc(1.2), isHardZero: false
        )

        let result = PathConflict.capped(
            decision, event: try car(), records: try records(sweepDeg: 60)
        )

        XCTAssertEqual(result.decision.driver, .ttc(1.2))
        XCTAssertFalse(result.decision.isHardZero)
    }

    // MARK: - Everything it must leave alone

    func testActSoonIsNeverCapped() throws {
        let decision = UrgencyDecision(urgency: UrgencyLevel.actSoon, driver: .band(.near))

        let result = PathConflict.capped(
            decision, event: try car(), records: try records(sweepDeg: 60)
        )

        XCTAssertFalse(result.capped)
        XCTAssertEqual(result.decision, decision)
    }

    func testAwareIsNeverCapped() throws {
        let decision = UrgencyDecision(urgency: UrgencyLevel.aware, driver: .band(.mid))

        let result = PathConflict.capped(
            decision, event: try car(), records: try records(sweepDeg: 60)
        )

        XCTAssertFalse(result.capped)
    }

    /// A pole cannot be on a collision course *with you* in the sense this
    /// gate models -- you walk into it. Static classes are capped at urgency 2
    /// by A7 anyway; this is the belt to that braces.
    func testAStaticClassIsNeverCapped() throws {
        let pole = try Make.event(
            source: .x4Cloud, trackId: "car", tCaptureMs: t0 + 500, cls: .pole,
            bearingDeg: 47, rangeBand: .near
        )

        let result = PathConflict.capped(stop, event: pole, records: try records(sweepDeg: 60))

        XCTAssertFalse(result.capped)
    }

    // MARK: - Fail open

    /// No evidence is not evidence of a miss. A first sighting keeps its Stop.
    func testAFirstSightingKeepsItsStop() throws {
        let only = AcceptedRecord(event: try car(), arrivalMs: t0 + 500)

        let result = PathConflict.capped(stop, event: try car(), records: [only])

        XCTAssertFalse(result.capped)
        XCTAssertEqual(result.decision, stop)
    }

    func testNoRecordsAtAllKeepsTheStop() throws {
        let result = PathConflict.capped(stop, event: try car(), records: [])

        XCTAssertFalse(result.capped)
    }

    /// Strictly greater: a sweep exactly at the threshold is not yet a miss.
    func testASweepExactlyAtTheThresholdIsNotCapped() throws {
        let sweep = PathConflict.sweepThresholdDegPerS / 2  // over half a second

        let result = PathConflict.capped(
            stop, event: try car(), records: try records(sweepDeg: sweep)
        )

        XCTAssertFalse(result.capped)
    }

    /// A torso turn moves every bearing at once. With two other tracks to
    /// measure it against, the turn is cancelled and the Stop survives.
    func testATorsoTurnDoesNotCapAHeadOnThreat() throws {
        // Everything sweeps 60 degrees: the wearer turned, nothing moved.
        let turning = try records(sweepDeg: 60, trackId: "car")
            + (try records(sweepDeg: 60, trackId: "other-1"))
            + (try records(sweepDeg: 60, trackId: "other-2"))

        let result = PathConflict.capped(stop, event: try car(), records: turning)

        XCTAssertFalse(result.capped, "common-mode rotation must not read as a miss")
    }
}
