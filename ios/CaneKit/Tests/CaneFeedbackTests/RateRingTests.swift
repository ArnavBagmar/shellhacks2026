import XCTest

@testable import CaneFeedback

/// A14: the §3.4 fallback trigger. One mechanism, two jobs -- the drops the
/// staleness gate already makes are the health probe. Exactly one counter
/// feeds it: `staleDrops`.
final class RateRingTests: XCTestCase {
    private let t0 = Make.t0

    func testEmptyRingHasZeroRateAndIsHealthy() {
        let ring = RateRing()
        XCTAssertEqual(ring.rate(now: t0), 0.0, accuracy: 1e-9)
        XCTAssertFalse(ring.isUnhealthy(now: t0))
    }

    func testRateIsStaleDropsOverReceivedInTheWindow() {
        var ring = RateRing()
        for index in 0..<10 {
            ring = ring.recordingReceived(now: t0 + Int64(index) * 100)
        }
        ring = ring.recordingStaleDrop(now: t0)
        ring = ring.recordingStaleDrop(now: t0 + 100)
        XCTAssertEqual(ring.rate(now: t0 + 900), 0.2, accuracy: 1e-9)
    }

    /// A single hot second is not a trend: the unhealthy verdict needs at
    /// least two *complete* buckets, so one bad frame cannot flip §3.4.
    func testUnhealthyNeedsTwoCompleteBuckets() {
        var ring = RateRing()
        for index in 0..<10 {
            ring = ring.recordingReceived(now: t0 + Int64(index) * 10)
            ring = ring.recordingStaleDrop(now: t0 + Int64(index) * 10)
        }
        XCTAssertEqual(ring.rate(now: t0 + 500), 1.0, accuracy: 1e-9)
        XCTAssertFalse(ring.isUnhealthy(now: t0 + 500), "same second: no complete bucket yet")

        ring = ring.recordingReceived(now: t0 + 1_000)
        XCTAssertFalse(ring.isUnhealthy(now: t0 + 1_500), "one complete bucket is still not a trend")

        ring = ring.recordingReceived(now: t0 + 2_000)
        XCTAssertTrue(ring.isUnhealthy(now: t0 + 2_500))
    }

    func testRateStaysHealthyBelowTheThreshold() {
        var ring = RateRing()
        for index in 0..<20 {
            ring = ring.recordingReceived(now: t0 + Int64(index) * 100)
        }
        ring = ring.recordingStaleDrop(now: t0)
        XCTAssertEqual(ring.rate(now: t0 + 2_500), 0.05, accuracy: 1e-9)
        XCTAssertFalse(ring.isUnhealthy(now: t0 + 2_500))
    }

    /// The ring is ten one-second buckets: a bucket from eleven seconds ago
    /// is overwritten, never counted twice.
    func testBucketsOlderThanTheWindowAreForgotten() {
        var ring = RateRing()
        ring = ring.recordingReceived(now: t0)
        ring = ring.recordingStaleDrop(now: t0)
        XCTAssertEqual(ring.rate(now: t0 + 500), 1.0, accuracy: 1e-9)

        let later = t0 + 11_000
        ring = ring.recordingReceived(now: later)
        XCTAssertEqual(ring.rate(now: later + 100), 0.0, accuracy: 1e-9)
    }

    /// Bounded state (A15): ten buckets, forever.
    func testRingStaysTenBucketsAfterThousandsOfSeconds() {
        var ring = RateRing()
        for second in 0..<5_000 {
            ring = ring.recordingReceived(now: t0 + Int64(second) * 1_000)
        }
        XCTAssertEqual(ring.bucketCount, RateRing.bucketCount)
    }
}
