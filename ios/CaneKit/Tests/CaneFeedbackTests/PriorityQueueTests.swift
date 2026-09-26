import CaneContracts
import XCTest

@testable import CaneFeedback

/// A11: keyed, bounded, ordered. One announcement at a time; higher urgency
/// preempts. The queue never replays the past -- that is the dequeue re-gate,
/// exercised through the manager.
final class PriorityQueueTests: XCTestCase {
    private func item(
        trackId: String,
        level: AnnouncementLevel,
        tCaptureMs: Int64,
        source: Source = .x4Local
    ) throws -> QueuedItem {
        let event = try Make.x4(
            source: source, trackId: trackId, tCaptureMs: tCaptureMs,
            cls: .cyclist, bearingDeg: 90.0, rangeBand: .near, closingMps: 4.0
        )
        return QueuedItem(event: event, level: level, driver: .band(.near))
    }

    func testOrderIsUrgencyDescendingThenCaptureAscending() throws {
        let older2 = try item(trackId: "a", level: .actSoon, tCaptureMs: Make.t0)
        let newer2 = try item(trackId: "b", level: .actSoon, tCaptureMs: Make.t0 + 100)
        let three = try item(trackId: "c", level: .immediate, tCaptureMs: Make.t0 + 200)

        var queue: [QueuedItem] = []
        for entry in [newer2, three, older2] {
            queue = AnnouncementQueue.inserting(entry, into: queue).queue
        }
        XCTAssertEqual(queue.map(\.key.trackId), ["c", "a", "b"])
    }

    func testOneItemPerKeyAndHigherUrgencyReplaces() throws {
        let aware = try item(trackId: "a", level: .aware, tCaptureMs: Make.t0)
        let actSoon = try item(trackId: "a", level: .actSoon, tCaptureMs: Make.t0)
        var queue = AnnouncementQueue.inserting(aware, into: []).queue
        queue = AnnouncementQueue.inserting(actSoon, into: queue).queue
        XCTAssertEqual(queue.count, 1)
        XCTAssertEqual(queue.first?.urgency, 2)
    }

    func testNewerCaptureReplacesTheSameKey() throws {
        let old = try item(trackId: "a", level: .actSoon, tCaptureMs: Make.t0)
        let new = try item(trackId: "a", level: .actSoon, tCaptureMs: Make.t0 + 300)
        var queue = AnnouncementQueue.inserting(old, into: []).queue
        queue = AnnouncementQueue.inserting(new, into: queue).queue
        XCTAssertEqual(queue.count, 1)
        XCTAssertEqual(queue.first?.tCaptureMs, Make.t0 + 300)
    }

    func testStaleRepeatOfTheSameKeyDoesNotDisplaceABetterPendingItem() throws {
        let good = try item(trackId: "a", level: .immediate, tCaptureMs: Make.t0 + 300)
        let worse = try item(trackId: "a", level: .aware, tCaptureMs: Make.t0)
        var queue = AnnouncementQueue.inserting(good, into: []).queue
        queue = AnnouncementQueue.inserting(worse, into: queue).queue
        XCTAssertEqual(queue.count, 1)
        XCTAssertEqual(queue.first?.urgency, 3)
    }

    func testNewerButLowerUrgencyRepeatDoesNotDisplaceAPendingStop() throws {
        // A jittery read on the next frame for the same key must never
        // silently swallow a queued Stop (review finding, step 9).
        let stop = try item(trackId: "a", level: .immediate, tCaptureMs: Make.t0)
        let jitter = try item(trackId: "a", level: .aware, tCaptureMs: Make.t0 + 300)
        var queue = AnnouncementQueue.inserting(stop, into: []).queue
        queue = AnnouncementQueue.inserting(jitter, into: queue).queue
        XCTAssertEqual(queue.count, 1)
        XCTAssertEqual(queue.first?.urgency, 3)
        XCTAssertEqual(queue.first?.tCaptureMs, Make.t0)
    }

    func testOverflowEvictsTheLowestUrgencyOldestItem() throws {
        var queue: [QueuedItem] = []
        for index in 0..<AnnouncementQueue.maxDepth {
            queue = AnnouncementQueue.inserting(
                try item(trackId: "a\(index)", level: .actSoon, tCaptureMs: Make.t0 + Int64(index)),
                into: queue
            ).queue
        }
        XCTAssertEqual(queue.count, AnnouncementQueue.maxDepth)

        let result = AnnouncementQueue.inserting(
            try item(trackId: "late", level: .actSoon, tCaptureMs: Make.t0 + 999),
            into: queue
        )
        XCTAssertTrue(result.overflowed)
        XCTAssertEqual(result.queue.count, AnnouncementQueue.maxDepth)
        XCTAssertFalse(result.queue.contains { $0.key.trackId == "a0" }, "the oldest act-soon lost")
        XCTAssertTrue(result.queue.contains { $0.key.trackId == "late" })
    }

    /// "An urgency-3 item is only ever evicted by another urgency-3 item."
    func testAFullQueueOfStopsRejectsALesserItemInsteadOfEvictingAStop() throws {
        var queue: [QueuedItem] = []
        for index in 0..<AnnouncementQueue.maxDepth {
            queue = AnnouncementQueue.inserting(
                try item(trackId: "s\(index)", level: .immediate, tCaptureMs: Make.t0 + Int64(index)),
                into: queue
            ).queue
        }
        let result = AnnouncementQueue.inserting(
            try item(trackId: "quiet", level: .actSoon, tCaptureMs: Make.t0 + 500),
            into: queue
        )
        XCTAssertTrue(result.overflowed)
        XCTAssertEqual(result.queue.count, AnnouncementQueue.maxDepth)
        XCTAssertFalse(result.queue.contains { $0.key.trackId == "quiet" })
        XCTAssertTrue(result.queue.allSatisfy { $0.urgency == 3 })
    }

    func testAStopEvictsTheOldestStopWhenEverythingIsAStop() throws {
        var queue: [QueuedItem] = []
        for index in 0..<AnnouncementQueue.maxDepth {
            queue = AnnouncementQueue.inserting(
                try item(trackId: "s\(index)", level: .immediate, tCaptureMs: Make.t0 + Int64(index)),
                into: queue
            ).queue
        }
        let result = AnnouncementQueue.inserting(
            try item(trackId: "fresh", level: .immediate, tCaptureMs: Make.t0 + 900),
            into: queue
        )
        XCTAssertTrue(result.overflowed)
        XCTAssertFalse(result.queue.contains { $0.key.trackId == "s0" })
        XCTAssertTrue(result.queue.contains { $0.key.trackId == "fresh" })
    }

    func testDedupLoserIsRemovedFromTheQueue() throws {
        let loser = try item(trackId: "loser", level: .actSoon, tCaptureMs: Make.t0)
        let keeper = try item(trackId: "keeper", level: .actSoon, tCaptureMs: Make.t0 + 10)
        var queue = AnnouncementQueue.inserting(loser, into: []).queue
        queue = AnnouncementQueue.inserting(keeper, into: queue).queue
        let pruned = AnnouncementQueue.removing(keys: [loser.key], from: queue)
        XCTAssertEqual(pruned.map(\.key.trackId), ["keeper"])
    }
}
