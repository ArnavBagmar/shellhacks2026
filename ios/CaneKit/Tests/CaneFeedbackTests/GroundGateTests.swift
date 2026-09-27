import CaneTransport
import XCTest

@testable import CaneFeedback

/// The laptop re-sends the same hole every tick for as long as it is in frame.
/// Ungated that is three sentences a second about one pothole.
final class GroundGateTests: XCTestCase {
    private let t0: Int64 = 1_790_000_000_000

    private func cue(bearingDeg: Double = 0.0, tCaptureMs: Int64? = nil) throws -> GroundCue {
        try GroundCue(
            tCaptureMs: tCaptureMs ?? t0,
            ttlMs: GroundLimits.ttlMs,
            bearingDeg: bearingDeg,
            spanDeg: 3.75,
            dropRatio: 2.4,
            confidence: 0.64,
            source: .x4Cloud
        )
    }

    func testTheFirstSightingIsAnnounced() throws {
        let (decision, state) = GroundGate.decide(try cue(), state: .init(), now: t0)

        XCTAssertEqual(decision, .announce)
        XCTAssertEqual(state.announcedAtMs, t0)
    }

    func testTheSameHoleIsNotRepeated() throws {
        let (_, after) = GroundGate.decide(try cue(), state: .init(), now: t0)

        let (decision, _) = GroundGate.decide(try cue(), state: after, now: t0 + 333)

        XCTAssertEqual(decision, .throttled)
    }

    func testItIsSaidAgainAfterTheRefractory() throws {
        let (_, after) = GroundGate.decide(try cue(), state: .init(), now: t0)

        let later = t0 + GroundGate.refractoryMs
        let (decision, _) = GroundGate.decide(
            try cue(tCaptureMs: later), state: after, now: later
        )

        XCTAssertEqual(decision, .announce)
    }

    /// A hazard well away from the last one is a different hazard, and waiting
    /// six seconds to mention it would be six seconds too late.
    func testAHazardSomewhereElseReArmsImmediately() throws {
        let (_, after) = GroundGate.decide(try cue(bearingDeg: 0), state: .init(), now: t0)

        let (decision, _) = GroundGate.decide(
            try cue(bearingDeg: GroundGate.rearmBearingDeg + 5, tCaptureMs: t0 + 333),
            state: after,
            now: t0 + 333
        )

        XCTAssertEqual(decision, .announce)
    }

    func testASmallBearingWanderDoesNotReArm() throws {
        let (_, after) = GroundGate.decide(try cue(bearingDeg: 0), state: .init(), now: t0)

        let (decision, _) = GroundGate.decide(
            try cue(bearingDeg: GroundGate.rearmBearingDeg - 5, tCaptureMs: t0 + 333),
            state: after,
            now: t0 + 333
        )

        XCTAssertEqual(decision, .throttled, "gait roll must not re-arm the cue")
    }

    func testAnExpiredCueIsDroppedAndLeavesTheStateAlone() throws {
        let stale = t0 + Int64(GroundLimits.ttlMs) + 1

        let (decision, state) = GroundGate.decide(try cue(), state: .init(), now: stale)

        XCTAssertEqual(decision, .stale)
        XCTAssertNil(state.announcedAtMs)
    }

    /// Wrapping, once, because a hazard can sit either side of the antimeridian
    /// when the wearer turns.
    func testTheReArmCheckWrapsAtTheAntimeridian() throws {
        let (_, after) = GroundGate.decide(try cue(bearingDeg: 179), state: .init(), now: t0)

        let (decision, _) = GroundGate.decide(
            try cue(bearingDeg: -179, tCaptureMs: t0 + 333), state: after, now: t0 + 333
        )

        XCTAssertEqual(decision, .throttled, "179 and -179 are 2 degrees apart")
    }
}
