import CaneContracts
import Foundation
import XCTest

@testable import CaneFeedback

/// The PRD's geometry table, walked through the whole gauntlet.
///
/// One vehicle, one speed, one sampling cadence. The **only** thing that
/// differs between the two tests is how far to the side the vehicle passes,
/// and that difference alone must decide whether the wearer is told to stop.
final class PassingCarTests: XCTestCase {
    private let t0 = Make.t0
    /// 30 mph.
    private let speedMps = 13.4
    /// The X4's rung-1 cadence.
    private let frameMs: Int64 = 333

    /// Where the vehicle is, in metres of road remaining before it draws level.
    private let approach: [Double] = [16, 11.5, 7, 2.5]

    /// One sighting of a vehicle `lateralM` to the side with `aheadM` still to
    /// run. Bearing, range band and closing rate all come from the geometry, so
    /// no number here is hand-tuned to produce an answer.
    private func sighting(
        lateralM: Double, aheadM: Double, tCaptureMs: Int64
    ) throws -> AwarenessEvent {
        let range = (lateralM * lateralM + aheadM * aheadM).squareRoot()
        let bearingDeg = atan2(lateralM, aheadM) * 180.0 / .pi
        // The X4 never states metres; it bands from box height.
        let band: RangeBand = range <= 5 ? .near : (range <= 15 ? .mid : .far)
        // Radial closing: the component of the vehicle's velocity along the
        // line of sight. This is the number `UrgencyPolicy` escalates on, and
        // it stays high for a vehicle that will miss by a mile.
        let closing = speedMps * aheadM / range

        return try Make.x4(
            source: .x4Cloud, trackId: "vehicle", tCaptureMs: tCaptureMs,
            cls: .car, confidence: 0.85, bearingDeg: bearingDeg,
            rangeBand: band, closingMps: closing
        )
    }

    private func walk(lateralM: Double) async throws -> (sink: RecordingSink, state: FeedbackState) {
        let sink = RecordingSink()
        let clock = TestClock(now: t0)
        let subject = FeedbackManager(sink: sink, clock: clock.reader)

        for (index, aheadM) in approach.enumerated() {
            let at = t0 + Int64(index) * frameMs
            clock.set(at)
            await subject.ingest(
                try sighting(lateralM: lateralM, aheadM: aheadM, tCaptureMs: at), now: at
            )
            await subject.settle()
        }
        return (sink, await subject.snapshot)
    }

    private func highestUrgency(_ sink: RecordingSink) -> Int {
        sink.presented.map(\.urgency).max() ?? 0
    }

    /// A vehicle passing 3.5 m away -- sidewalk to the near lane. Its radial
    /// closing rate is 7.8 m/s at the moment it reads `near`, which is what
    /// makes `UrgencyPolicy` call it a Stop. Its bearing is sweeping at
    /// ~84 deg/s, which is what makes that wrong.
    func testAVehiclePassingOnTheRoadIsNeverAStop() async throws {
        let (sink, state) = try await walk(lateralM: 3.5)

        XCTAssertEqual(
            highestUrgency(sink), UrgencyLevel.actSoon,
            "a vehicle that will miss by 3.5 m must never reach urgency 3"
        )
        XCTAssertGreaterThan(state.counters.pathConflictCapped, 0)
    }

    /// The wearer is still told about it. Suppressing the Stop must not
    /// suppress the warning -- they hear the direction and the class.
    func testTheWearerIsStillWarnedAboutThePassingVehicle() async throws {
        let (sink, _) = try await walk(lateralM: 3.5)

        let spoken = sink.presented.filter { $0.speech != nil }
        XCTAssertFalse(spoken.isEmpty, "a capped Stop must not become silence")
        XCTAssertEqual(spoken.last?.cls, .car)
    }

    /// The same vehicle, the same speed, on a collision course. Its bearing
    /// barely moves, and it must still stop the wearer.
    func testAVehicleOnACollisionCourseStillStopsTheWearer() async throws {
        let (sink, state) = try await walk(lateralM: 0.1)

        XCTAssertEqual(
            highestUrgency(sink), UrgencyLevel.immediate,
            "a head-on vehicle must still reach urgency 3"
        )
        XCTAssertEqual(
            state.counters.pathConflictCapped, 0,
            "nothing about a head-on approach should look like a miss"
        )
    }
}
