import CaneContracts
import XCTest

@testable import CaneFeedback

/// Stage 8 (A11): dequeue, re-gate staleness, throttle, preempt, present.
/// This is where "never announces late" is actually enforced -- an event can
/// go stale while it waits behind an utterance.
final class PumpTests: XCTestCase {
    private let t0 = Make.t0

    private func pole(
        trackId: String,
        tCaptureMs: Int64,
        rangeM: Double,
        bearingDeg: Double = 0.0,
        cls: ObjectClass = .pole
    ) throws -> AwarenessEvent {
        try Make.phone(
            trackId: trackId, tCaptureMs: tCaptureMs, cls: cls,
            bearingDeg: bearingDeg, rangeM: rangeM
        )
    }

    /// A17: `presented.map(\.urgency) == [2, 3]`, and the announcement that
    /// was in progress is the one that got cancelled.
    func testHigherUrgencyPreemptsAnAnnouncementInProgress() async throws {
        let sink = RecordingSink(presentDurationMs: 400)
        let clock = TestClock(now: t0)
        let subject = FeedbackManager(sink: sink, clock: clock.reader)

        let actSoon = try pole(trackId: "pole-1", tCaptureMs: t0, rangeM: 1.5)
        await subject.ingest(actSoon, now: t0)
        await sink.waitUntilPresenting(atLeast: 1)

        let stop = try Make.phone(
            trackId: "cyclist-1", tCaptureMs: t0 + 50, cls: .cyclist,
            bearingDeg: 30.0, rangeM: 3.0, closingMps: 2.0
        )
        clock.set(t0 + 50)
        await subject.ingest(stop, now: t0 + 50)
        await subject.settle()

        XCTAssertEqual(sink.presented.map(\.urgency), [2, 3])
        let interrupted = try XCTUnwrap(sink.presented.first)
        XCTAssertTrue(sink.cancelled.contains(interrupted.id), "the in-progress utterance is cut")
        let counters = await subject.counters
        XCTAssertEqual(counters.preempted, 1)
    }

    /// A17: a preempted announcement is dropped, never resumed or re-queued.
    func testAPreemptedAnnouncementIsNotReplayed() async throws {
        let sink = RecordingSink(presentDurationMs: 400)
        let clock = TestClock(now: t0)
        let subject = FeedbackManager(sink: sink, clock: clock.reader)

        let actSoon = try pole(trackId: "pole-1", tCaptureMs: t0, rangeM: 1.5)
        await subject.ingest(actSoon, now: t0)
        await sink.waitUntilPresenting(atLeast: 1)
        let stop = try Make.phone(
            trackId: "cyclist-1", tCaptureMs: t0 + 50, cls: .cyclist,
            bearingDeg: 30.0, rangeM: 3.0, closingMps: 2.0
        )
        clock.set(t0 + 50)
        await subject.ingest(stop, now: t0 + 50)
        clock.set(t0 + 5_000)
        await subject.tick(now: t0 + 5_000)
        await subject.settle()

        let preemptedId = try XCTUnwrap(sink.presented.first).id
        XCTAssertEqual(sink.presented.filter { $0.id == preemptedId }.count, 1)
        let state = await subject.snapshot
        XCTAssertTrue(state.queue.isEmpty)
    }

    /// Two act-soon announcements play in `t_capture_ms` order, one at a
    /// time, separated by the speech gap.
    func testQueuedAnnouncementsPlayInCaptureOrderRespectingTheSpeechGap() async throws {
        let sink = RecordingSink(presentDurationMs: 100)
        let clock = TestClock(now: t0)
        let subject = FeedbackManager(sink: sink, clock: clock.reader)

        let first = try pole(trackId: "in-flight", tCaptureMs: t0 + 50, rangeM: 1.5, bearingDeg: 0.0)
        await subject.ingest(first, now: t0 + 50)
        await sink.waitUntilPresenting(atLeast: 1)

        let later = try pole(trackId: "later", tCaptureMs: t0 + 20, rangeM: 1.5, bearingDeg: 60.0, cls: .wall)
        clock.set(t0 + 60)
        await subject.ingest(later, now: t0 + 60)

        let earlier = try pole(trackId: "earlier", tCaptureMs: t0 + 10, rangeM: 1.5, bearingDeg: -60.0, cls: .door)
        clock.set(t0 + 70)
        await subject.ingest(earlier, now: t0 + 70)

        clock.set(t0 + 2_000)
        await subject.settle()
        clock.set(t0 + 4_000)
        await subject.tick(now: t0 + 4_000)
        await subject.settle()

        XCTAssertEqual(sink.presented.map(\.trackId), ["in-flight", "earlier", "later"])
    }

    /// A17: dequeue-time staleness with a busy sink. A 500 ms cyclist behind
    /// a 200 ms utterance and a clock that has moved on is dropped, not
    /// announced late -- and the drop does *not* feed the §3.4 trigger.
    func testAnItemThatGoesStaleWhileQueuedIsDiscardedAtDequeue() async throws {
        let sink = RecordingSink(presentDurationMs: 200)
        let clock = TestClock(now: t0)
        let subject = FeedbackManager(sink: sink, clock: clock.reader)

        let busy = try pole(trackId: "pole-1", tCaptureMs: t0, rangeM: 4.0)
        await subject.ingest(busy, now: t0)
        await sink.waitUntilPresenting(atLeast: 1)

        let cyclist = try Make.x4(
            trackId: "cyclist-1", tCaptureMs: t0 + 10, cls: .cyclist,
            bearingDeg: 172.0, rangeBand: .far, closingMps: 4.0
        )
        clock.set(t0 + 10)
        await subject.ingest(cyclist, now: t0 + 10)

        clock.set(t0 + 700)
        await subject.settle()

        XCTAssertEqual(sink.presented.map(\.trackId), ["pole-1"])
        let counters = await subject.counters
        XCTAssertEqual(counters.queueExpired, 1)
        XCTAssertEqual(counters.staleDrops, 0, "a queue expiry is not a transport health signal")
    }

    /// A17: a wedged renderer must not stall the queue for ever.
    func testAHungSinkTimesOutAndTheQueueKeepsMoving() async throws {
        let sink = RecordingSink(presentDurationMs: 4_000, ignoresCancellation: true)
        let clock = TestClock(now: t0)
        let subject = FeedbackManager(sink: sink, presentTimeoutMs: 120, clock: clock.reader)

        let first = try pole(trackId: "pole-1", tCaptureMs: t0, rangeM: 4.0, bearingDeg: 0.0)
        await subject.ingest(first, now: t0)
        let second = try pole(trackId: "wall-1", tCaptureMs: t0 + 10, rangeM: 4.0, bearingDeg: 60.0, cls: .wall)
        clock.set(t0 + 10)
        await subject.ingest(second, now: t0 + 10)
        await subject.settle()

        XCTAssertEqual(sink.presented.count, 2)
        let counters = await subject.counters
        XCTAssertEqual(counters.sinkTimeouts, 2)
    }

    /// A16: stages return intents. The manager is the only thing that ever
    /// touches a sink, so every decision has to be expressible as data.
    func testDecisionsAreExpressedAsEffects() throws {
        let event = try Make.phone(trackId: "pole-1", tCaptureMs: t0, cls: .pole, rangeM: 1.5)
        let announcement = Announcement(id: 9, level: .actSoon, event: event, decidedAtMs: t0)
        XCTAssertEqual(Pump.Decision.idle.effects, [])
        XCTAssertEqual(Pump.Decision.present(announcement).effects, [.present(announcement)])
        XCTAssertEqual(
            Pump.Decision.preempt(cancelling: 8, with: announcement).effects,
            [.cancelCurrent, .present(announcement)]
        )
        XCTAssertEqual(Gauntlet.Outcome.enqueued.effects, [])
        XCTAssertEqual(Gauntlet.Outcome.dropped(.stale).effects, [])
        XCTAssertEqual(Gauntlet.Outcome.ambient(event, 0).effects, [.ambient(event, 0)])
    }

    func testTheProductionPresentTimeoutIsTwoAndAHalfSeconds() {
        XCTAssertEqual(FeedbackManager.defaultPresentTimeoutMs, 2_500)
    }

    /// A17: a Stop gets through a full queue, an active per-key refractory
    /// and an active spatial refractory.
    func testUrgencyThreePenetratesAFullQueueAndBothRefractories() async throws {
        let sink = RecordingSink(presentDurationMs: 600)
        let clock = TestClock(now: t0)
        let subject = FeedbackManager(sink: sink, clock: clock.reader)

        // An act-soon on the target track: leaves a live refractory record
        // and occupies the sink.
        let firstPass = try Make.x4(
            trackId: "target", tCaptureMs: t0, cls: .cyclist,
            bearingDeg: 170.0, rangeBand: .mid, closingMps: 4.0
        )
        await subject.ingest(firstPass, now: t0)
        await sink.waitUntilPresenting(atLeast: 1)

        // Fill the queue with act-soon chatter on other keys.
        let bearings: [Double] = [0, 45, 135, -45, -135, 20, 65, 110, 155]
        for (index, bearing) in bearings.enumerated() {
            let at = t0 + Int64(index) + 1
            clock.set(at)
            let chatter = try Make.x4(
                trackId: "chatter-\(index)", tCaptureMs: at, cls: .person,
                bearingDeg: bearing, rangeBand: .near, closingMps: 1.0
            )
            await subject.ingest(chatter, now: at)
        }
        let queuedDepth = await subject.snapshot.queue.count
        XCTAssertEqual(queuedDepth, AnnouncementQueue.maxDepth, "the queue is full")

        // The same object, now inside the collision zone.
        let stop = try Make.x4(
            trackId: "target", tCaptureMs: t0 + 20, cls: .cyclist,
            bearingDeg: 171.0, rangeBand: .near, closingMps: 6.0
        )
        // Both refractories really are live at this instant: an act-soon
        // version of this event would be suppressed on the key, and one from
        // any other key would be suppressed spatially.
        let live = await subject.snapshot
        XCTAssertEqual(
            RefractoryGate.check(
                stop, urgency: 2, record: live.tracks[FeedbackKey(stop)],
                announcements: live.recentAnnouncements, now: t0 + 20
            ),
            .suppressedByKey
        )
        let otherKey = try Make.x4(
            source: .x4Cloud, trackId: "ghost", tCaptureMs: t0 + 20, cls: .cyclist,
            bearingDeg: 171.0, rangeBand: .near, closingMps: 6.0
        )
        XCTAssertEqual(
            RefractoryGate.check(
                otherKey, urgency: 2, record: nil,
                announcements: live.recentAnnouncements, now: t0 + 20
            ),
            .suppressedSpatially
        )
        clock.set(t0 + 20)
        await subject.ingest(stop, now: t0 + 20)
        await subject.settle()

        XCTAssertEqual(sink.presented.last?.urgency, 3, "a Stop is never queued behind chatter")
        XCTAssertEqual(sink.presented.last?.speech, .oneWord)
    }
}
