import CaneContracts
import XCTest

@testable import CaneFeedback

/// The gauntlet end to end (A13, A16). Built against the golden fixture with
/// no camera and no cloud, exactly as F4 promises.
final class FeedbackManagerTests: XCTestCase {
    private let t0 = Make.t0

    private func manager(
        sink: RecordingSink,
        ambient: RecordingAmbientSink = RecordingAmbientSink(),
        clock: TestClock
    ) -> FeedbackManager {
        FeedbackManager(sink: sink, ambientSink: ambient, clock: clock.reader)
    }

    // MARK: - Acceptance: the fixture suite, malformed payloads included

    func testEveryFixtureCaseRunsThroughTheGauntletWithoutTrapping() async throws {
        let (cases, malformed) = try FeedbackFixturePaths.load()
        let sink = RecordingSink()
        let clock = TestClock(now: t0)
        let subject = manager(sink: sink, clock: clock)

        // Past every capture stamp in the fixture, so nothing is
        // future-dated; most valid cases are then stale, which is itself
        // the point -- the gauntlet drops them without trapping.
        let now = t0 + 60_000
        var expectedInvalid = 0
        for goldenCase in cases {
            if !goldenCase.expectValid { expectedInvalid += 1 }
            await subject.ingest(goldenCase.json, now: now)
        }
        for payload in malformed {
            expectedInvalid += 1
            await subject.ingest(payload, now: now)
        }
        await subject.settle()

        let counters = await subject.counters
        XCTAssertEqual(counters.eventsReceived, cases.count + malformed.count)
        XCTAssertEqual(counters.invalidRejected, expectedInvalid)
        XCTAssertEqual(counters.futureDated, 0, "the fixture is stamped in the past relative to now")
    }

    // MARK: - Anti-flooding (§4.7.4, the runbook's acceptance criterion)

    func testAFloodOfOneTrackYieldsExactlyOneAnnouncement() async throws {
        let sink = RecordingSink()
        let clock = TestClock(now: t0)
        let subject = manager(sink: sink, clock: clock)

        for index in 0..<30 {
            let at = t0 + Int64(index) * 33
            clock.set(at)
            let event = try Make.x4(
                trackId: "flood-1", tCaptureMs: at, cls: .cyclist,
                bearingDeg: 120.0, rangeBand: .mid, closingMps: 4.0
            )
            await subject.ingest(event, now: at)
        }
        await subject.settle()

        XCTAssertEqual(sink.presented.count, 1)
        XCTAssertEqual(sink.presented.first?.urgency, 2)
        let counters = await subject.counters
        XCTAssertEqual(counters.refractorySuppressed, 29)
    }

    func testAnEscalationOnTheSameTrackSpeaksImmediately() async throws {
        let sink = RecordingSink()
        let clock = TestClock(now: t0)
        let subject = manager(sink: sink, clock: clock)

        let actSoon = try Make.x4(
            trackId: "flood-1", tCaptureMs: t0, cls: .cyclist,
            bearingDeg: 120.0, rangeBand: .mid, closingMps: 4.0
        )
        await subject.ingest(actSoon, now: t0)
        await subject.settle()
        let immediate = try Make.x4(
            trackId: "flood-1", tCaptureMs: t0 + 300, cls: .cyclist,
            bearingDeg: 120.0, rangeBand: .near, closingMps: 4.0
        )
        clock.set(t0 + 300)
        await subject.ingest(immediate, now: t0 + 300)
        await subject.settle()

        XCTAssertEqual(sink.presented.map(\.urgency), [2, 3], "urgency 3 has refractory 0")
    }

    /// A17: the same `track_id` from two producers is two objects, placed at
    /// clearly different bearings so nothing spatial is in play.
    func testSameTrackIdOnTwoSourcesYieldsTwoAnnouncements() async throws {
        let sink = RecordingSink()
        let clock = TestClock(now: t0)
        let subject = manager(sink: sink, clock: clock)

        let phone = try Make.phone(
            trackId: "7", tCaptureMs: t0, cls: .cyclist, bearingDeg: 0.0, rangeM: 3.0, closingMps: 2.0
        )
        await subject.ingest(phone, now: t0)
        let x4 = try Make.x4(
            trackId: "7", tCaptureMs: t0 + 100, cls: .cyclist,
            bearingDeg: 120.0, rangeBand: .near, closingMps: 4.0
        )
        clock.set(t0 + 100)
        await subject.ingest(x4, now: t0 + 100)
        await subject.settle()

        XCTAssertEqual(sink.presented.count, 2)
        XCTAssertEqual(Set(sink.presented.map(\.source)), [.phone, .x4Local])
    }

    // MARK: - Acceptance: urgency 1 is haptic only

    func testAwareEventProducesHapticWithoutSpeech() async throws {
        let sink = RecordingSink()
        let clock = TestClock(now: t0)
        let subject = manager(sink: sink, clock: clock)

        let pole = try Make.phone(trackId: "pole-1", tCaptureMs: t0, cls: .pole, bearingDeg: 10.0, rangeM: 4.0)
        await subject.ingest(pole, now: t0)
        await subject.settle()

        XCTAssertEqual(sink.presented.count, 1)
        XCTAssertNil(sink.presented.first?.speech)
        XCTAssertEqual(sink.presented.first?.haptic, .directional)
    }

    func testAmbientEventIsLoggedAndNeverAnnounced() async throws {
        let sink = RecordingSink()
        let ambient = RecordingAmbientSink()
        let clock = TestClock(now: t0)
        let subject = manager(sink: sink, ambient: ambient, clock: clock)

        let distantPole = try Make.phone(trackId: "pole-2", tCaptureMs: t0, cls: .pole, bearingDeg: 10.0, rangeM: 9.0)
        await subject.ingest(distantPole, now: t0)
        await subject.settle()

        XCTAssertTrue(sink.presented.isEmpty)
        XCTAssertEqual(ambient.events.count, 1)
        let counters = await subject.counters
        XCTAssertEqual(counters.ambientLogged, 1)
    }

    // MARK: - Counters and the §3.4 health signal (A14)

    func testStaleDropsAreCountedAndFeedTheRate() async throws {
        let sink = RecordingSink()
        let clock = TestClock(now: t0)
        let subject = manager(sink: sink, clock: clock)

        for index in 0..<8 {
            let at = t0 + Int64(index) * 100
            let fresh = try Make.x4(
                trackId: "fresh-\(index)", tCaptureMs: at, cls: .person,
                bearingDeg: Double(index) * 20.0 - 80.0, rangeBand: .far
            )
            await subject.ingest(fresh, now: at)
        }
        for index in 0..<2 {
            let capture = t0 + Int64(index) * 100
            let late = try Make.x4(
                trackId: "late-\(index)", tCaptureMs: capture, cls: .cyclist,
                bearingDeg: 150.0, rangeBand: .mid, closingMps: 4.0
            )
            await subject.ingest(late, now: capture + 600)
        }
        await subject.settle()

        let counters = await subject.counters
        XCTAssertEqual(counters.staleDrops, 2)
        XCTAssertEqual(counters.staleDropsByClass[.cyclist], 2)
        XCTAssertEqual(counters.staleDropsByWouldBeUrgency[2], 2, "a drop still scores, for the health signal")
        let rate = await subject.staleDropRate(now: t0 + 900)
        XCTAssertEqual(rate, 0.2, accuracy: 1e-9)
    }

    func testFutureDatedIsCountedSeparatelyFromStale() async throws {
        let sink = RecordingSink()
        let clock = TestClock(now: t0)
        let subject = manager(sink: sink, clock: clock)

        let ahead = try Make.x4(trackId: "ahead", tCaptureMs: t0 + 5_000, cls: .person, rangeBand: .near)
        await subject.ingest(ahead, now: t0)
        await subject.settle()

        let counters = await subject.counters
        XCTAssertEqual(counters.futureDated, 1)
        XCTAssertEqual(counters.staleDrops, 0, "a bad clock offset must never trigger the §3.4 fallback")
        let rate = await subject.staleDropRate(now: t0)
        XCTAssertEqual(rate, 0.0)
    }

    func testExactReplayOfTheSameEventIsDroppedOnce() async throws {
        let sink = RecordingSink()
        let clock = TestClock(now: t0)
        let subject = manager(sink: sink, clock: clock)

        let event = try Make.x4(
            trackId: "replay", tCaptureMs: t0, cls: .cyclist,
            bearingDeg: 120.0, rangeBand: .near, closingMps: 4.0
        )
        await subject.ingest(event, now: t0)
        await subject.ingest(event, now: t0 + 50)
        await subject.settle()

        XCTAssertEqual(sink.presented.count, 1)
        let counters = await subject.counters
        XCTAssertEqual(counters.duplicateExact, 1)
    }

    /// A17: the failover case, as a spatial-refractory test. The cloud says
    /// Stop for a cyclist at 170; the local detector reports the same object
    /// under its own track id at 172 half a second later. One object, one
    /// Stop.
    func testCloudThenLocalStopForTheSameObjectSpeaksOnce() async throws {
        let sink = RecordingSink()
        let clock = TestClock(now: t0)
        let subject = manager(sink: sink, clock: clock)

        let cloud = try Make.x4(
            source: .x4Cloud, trackId: "c1", tCaptureMs: t0, cls: .cyclist,
            bearingDeg: 170.0, rangeBand: .near, closingMps: 6.0
        )
        await subject.ingest(cloud, now: t0)
        await subject.settle()

        // 400 ms apart: outside the 300 ms dedup window, so this is the
        // spatial refractory doing the work, not gate 4.
        let local = try Make.x4(
            source: .x4Local, trackId: "l1", tCaptureMs: t0 + 400, cls: .cyclist,
            bearingDeg: 172.0, rangeBand: .near, closingMps: 6.0
        )
        clock.set(t0 + 400)
        await subject.ingest(local, now: t0 + 400)
        await subject.settle()

        XCTAssertEqual(sink.presented.count, 1)
        XCTAssertEqual(sink.presented.first?.urgency, 3)
        let counters = await subject.counters
        XCTAssertEqual(counters.spatialSuppressed, 1)
        XCTAssertEqual(counters.deduped, 0, "the capture stamps are 400 ms apart")
    }

    // MARK: - Bounded state and concurrency (A15, A13)

    func testStateStaysBoundedAfterTenThousandEvents() async throws {
        let sink = RecordingSink()
        let clock = TestClock(now: t0)
        let subject = manager(sink: sink, clock: clock)

        for index in 0..<10_000 {
            let at = t0 + Int64(index) * 10
            clock.set(at)
            let event = try Make.x4(
                trackId: "bulk-\(index)", tCaptureMs: at, cls: .person,
                bearingDeg: Double(index % 360) - 179.0, rangeBand: .near, closingMps: 1.0
            )
            await subject.ingest(event, now: at)
        }
        await subject.settle()

        let state = await subject.snapshot
        XCTAssertLessThanOrEqual(state.recentAccepted.count, RecentAccepted.maxRecords)
        XCTAssertLessThanOrEqual(state.tracks.count, TrackRecords.maxRecords)
        XCTAssertLessThanOrEqual(state.recentAnnouncements.count, RecentAnnouncements.maxRecords)
        XCTAssertLessThanOrEqual(state.queue.count, AnnouncementQueue.maxDepth)
    }

    func testConcurrentIngestsKeepBothTrackRecords() async throws {
        let sink = RecordingSink()
        let clock = TestClock(now: t0)
        let subject = manager(sink: sink, clock: clock)

        let left = try Make.x4(
            trackId: "left", tCaptureMs: t0, cls: .cyclist,
            bearingDeg: -90.0, rangeBand: .near, closingMps: 4.0
        )
        let right = try Make.x4(
            trackId: "right", tCaptureMs: t0, cls: .car,
            bearingDeg: 90.0, rangeBand: .near, closingMps: 4.0
        )
        async let first: Void = subject.ingest(left, now: t0)
        async let second: Void = subject.ingest(right, now: t0)
        _ = await (first, second)
        await subject.settle()

        let state = await subject.snapshot
        XCTAssertNotNil(state.tracks[FeedbackKey(left)])
        XCTAssertNotNil(state.tracks[FeedbackKey(right)])
        XCTAssertEqual(sink.presented.count, 2)
    }
}
