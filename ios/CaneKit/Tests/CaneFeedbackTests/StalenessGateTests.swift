import CaneContracts
import XCTest

@testable import CaneFeedback

/// Gate 2 (A1.2 / §4.4). "The feedback manager drops. It never announces
/// late." The effective TTL is `min(producer ttl, class ttl)`, so a producer
/// can be more conservative than the table but never more optimistic.
final class StalenessGateTests: XCTestCase {
    /// The §4.4 table, restated independently of the implementation: if the
    /// two ever disagree, one of them is a bug.
    private let expectedTtlMs: [ObjectClass: Int64] = [
        .person: 1500,
        .cyclist: 500,
        .scooter: 500,
        .car: 400,
        .bus: 400,
        .pole: 8000,
        .bollard: 8000,
        .wall: 8000,
        .parkedCar: 8000,
        .door: 8000,
        .overhang: 8000,
        .unknownObstacle: 1500,
    ]

    func testTTLTableCoversEveryClassWithTheSpecValues() {
        for cls in ObjectClass.allCases {
            XCTAssertEqual(TTLTable.ttlMs(for: cls), expectedTtlMs[cls], "ttl for \(cls.rawValue)")
        }
        XCTAssertEqual(expectedTtlMs.count, ObjectClass.allCases.count)
    }

    func testCyclistSixHundredMillisecondsOldIsDropped() throws {
        let event = try Make.x4(tCaptureMs: Make.t0, cls: .cyclist)
        XCTAssertTrue(StalenessGate.isStale(event, now: Make.t0 + 600))
    }

    func testCyclistExactlyAtItsTTLIsStillFresh() throws {
        let event = try Make.x4(tCaptureMs: Make.t0, cls: .cyclist)
        XCTAssertFalse(StalenessGate.isStale(event, now: Make.t0 + 500))
        XCTAssertTrue(StalenessGate.isStale(event, now: Make.t0 + 501))
    }

    func testPoleSixSecondsOldStillPasses() throws {
        let event = try Make.x4(tCaptureMs: Make.t0, cls: .pole, rangeBand: .near)
        XCTAssertFalse(StalenessGate.isStale(event, now: Make.t0 + 6_000))
        XCTAssertTrue(StalenessGate.isStale(event, now: Make.t0 + 8_001))
    }

    /// F3 / A17: a producer `ttl_ms` of 15000 on a cyclist is clamped to the
    /// class TTL of 500 ms -- the optimistic producer does not get to keep a
    /// stale bearing alive.
    func testProducerTtlIsClampedDownToTheClassTtl() throws {
        let event = try Make.x4(tCaptureMs: Make.t0, cls: .cyclist, ttlMs: 15_000)
        XCTAssertEqual(TTLTable.clampedTtlMs(for: event), 500)
        XCTAssertTrue(StalenessGate.isStale(event, now: Make.t0 + 600))
    }

    /// The clamp is a `min`, not an override: a producer that knows its
    /// estimate is short-lived stays short-lived.
    func testProducerTtlShorterThanTheClassTtlWins() throws {
        let event = try Make.x4(tCaptureMs: Make.t0, cls: .pole, rangeBand: .near, ttlMs: 200)
        XCTAssertEqual(TTLTable.clampedTtlMs(for: event), 200)
        XCTAssertTrue(StalenessGate.isStale(event, now: Make.t0 + 300))
    }

    func testExpiryInstantIsCaptureTimePlusClampedTtl() throws {
        let event = try Make.x4(tCaptureMs: Make.t0, cls: .car, ttlMs: 15_000)
        XCTAssertEqual(TTLTable.expiresAtMs(event), Make.t0 + 400)
    }

    /// An event that arrives before its own capture stamp (inside the
    /// future-dating tolerance) is not stale.
    func testSlightlyFutureDatedEventIsNotStale() throws {
        let event = try Make.x4(tCaptureMs: Make.t0, cls: .cyclist)
        XCTAssertFalse(StalenessGate.isStale(event, now: Make.t0 - 100))
    }
}
