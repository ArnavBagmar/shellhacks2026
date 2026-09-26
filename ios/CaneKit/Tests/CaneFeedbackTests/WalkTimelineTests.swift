import CaneContracts
import XCTest

@testable import CaneFeedback

/// A17's four-object walk: the demo, as a test. The wearer walks at 1.4 m/s
/// towards a pole the phone can range, an overtaking cyclist the X4 sees in
/// bands only, a pedestrian crossing from the X4's sector into the phone's,
/// and a parked car behind them that must never be mentioned.
final class WalkTimelineTests: XCTestCase {
    private let t0 = Make.t0
    /// The wearer's pace: the pole closes 1.4 m every second.
    private let paceMps = 1.4

    func testFourObjectWalkProducesTheExpectedAnnouncementSequence() async throws {
        let sink = RecordingSink()
        let ambient = RecordingAmbientSink()
        let clock = TestClock(now: t0)
        let subject = FeedbackManager(sink: sink, ambientSink: ambient, clock: clock.reader)

        /// Each announcement in this timeline finishes long before the next
        /// event arrives, so `settle()` stands in for the renderer having
        /// stopped talking.
        func ingest(_ event: AwarenessEvent, at offset: Int64) async {
            clock.set(t0 + offset)
            await subject.ingest(event, now: t0 + offset)
            await subject.settle()
        }

        // Object D: a parked car behind the wearer. Seen throughout, never
        // announced -- you cannot walk into what is behind you.
        await ingest(
            try Make.x4(
                trackId: "x4-parked", tCaptureMs: t0, cls: .parkedCar,
                bearingDeg: -160.0, rangeBand: .near
            ),
            at: 0
        )

        // Object A: a pole dead ahead, phone-ranged, closing at walking pace.
        for step in 0...5 {
            let offset = Int64(step) * 1_000
            let metres = 8.0 - paceMps * Double(step)
            await ingest(
                try Make.phone(
                    trackId: "phone-pole", tCaptureMs: t0 + offset, cls: .pole,
                    bearingDeg: 3.0, rangeM: metres
                ),
                at: offset
            )
        }

        // Object B: a cyclist overtaking from behind, X4 bands only.
        await ingest(
            try Make.x4(
                trackId: "x4-cyclist", tCaptureMs: t0 + 7_000, cls: .cyclist,
                bearingDeg: 172.0, rangeBand: .far, closingMps: 5.0
            ),
            at: 7_000
        )
        await ingest(
            try Make.x4(
                trackId: "x4-cyclist", tCaptureMs: t0 + 7_500, cls: .cyclist,
                bearingDeg: 168.0, rangeBand: .mid, closingMps: 5.0
            ),
            at: 7_500
        )
        await ingest(
            try Make.x4(
                trackId: "x4-cyclist", tCaptureMs: t0 + 8_000, cls: .cyclist,
                bearingDeg: 160.0, rangeBand: .near, closingMps: 5.0
            ),
            at: 8_000
        )

        // Object C: a pedestrian crossing, seen first by the X4 at 50 deg...
        await ingest(
            try Make.x4(
                trackId: "x4-person", tCaptureMs: t0 + 9_500, cls: .person,
                bearingDeg: 50.0, rangeBand: .near, closingMps: 1.2
            ),
            at: 9_500
        )
        // ...then by the phone as it enters the collision zone. §4.5 handoff:
        // the same pedestrian, a new key -- and silence.
        await ingest(
            try Make.phone(
                trackId: "phone-person", tCaptureMs: t0 + 10_500, cls: .person,
                bearingDeg: 25.0, rangeM: 4.0, closingMps: 1.2
            ),
            at: 10_500
        )

        await subject.settle()

        let heard = sink.presented.map { ($0.cls, $0.urgency, $0.speech) }
        let expected: [(ObjectClass, Int, SpeechVerbosity?)] = [
            (.pole, 1, nil),
            (.pole, 2, .brief),
            (.cyclist, 1, nil),
            (.cyclist, 2, .brief),
            (.cyclist, 3, .oneWord),
            (.person, 2, .brief),
        ]
        XCTAssertEqual(heard.count, expected.count, "sequence was \(sink.presented.map { "\($0.cls.rawValue)/\($0.urgency)" })")
        for (index, pair) in zip(heard, expected).enumerated() {
            XCTAssertEqual(pair.0.0, pair.1.0, "step \(index) class")
            XCTAssertEqual(pair.0.1, pair.1.1, "step \(index) urgency")
            XCTAssertEqual(pair.0.2, pair.1.2, "step \(index) speech")
        }

        let counters = await subject.counters
        XCTAssertEqual(counters.spatialSuppressed, 1, "the X4-to-phone handoff is the only spatial suppression")
        XCTAssertGreaterThanOrEqual(counters.refractorySuppressed, 1)
        XCTAssertTrue(
            ambient.events.contains { $0.cls == .parkedCar },
            "the parked car behind the wearer goes to the hazard map, not the ears"
        )
        XCTAssertFalse(sink.presented.contains { $0.cls == .parkedCar })
    }
}
