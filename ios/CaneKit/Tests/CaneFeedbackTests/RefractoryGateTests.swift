import CaneContracts
import XCTest

@testable import CaneFeedback

/// Gate 6 (§4.7.4 as amended by A6 and A10). The anti-flooding mechanism,
/// widened from "this track id" to "this object, however it is keyed":
/// per-key, spatial, and the §4.5 X4-to-phone handoff are one table.
final class RefractoryGateTests: XCTestCase {
    private let t0 = Make.t0

    private func track(urgency: Int, at presentedAtMs: Int64) -> TrackRecord {
        TrackRecord(
            lastUrgency: urgency, lastDriver: .band(.near),
            presentedAtMs: presentedAtMs, lastSeenMs: presentedAtMs
        )
    }

    private func announcement(
        source: Source = .x4Local,
        trackId: String = "other",
        cls: ObjectClass = .cyclist,
        bearingDeg: Double = 170.0,
        urgency: Int = 2,
        at presentedAtMs: Int64
    ) -> AnnouncementRecord {
        AnnouncementRecord(
            key: FeedbackKey(source: source, trackId: trackId),
            cls: cls, bearingDeg: bearingDeg, urgency: urgency, presentedAtMs: presentedAtMs
        )
    }

    // MARK: - Per-key refractory (A10)

    func testSameKeyAtTheSameLevelIsSuppressedInsideTheWindow() throws {
        let event = try Make.x4(trackId: "t", cls: .cyclist, bearingDeg: 170.0, rangeBand: .near)
        XCTAssertEqual(
            RefractoryGate.check(
                event, urgency: 2, record: track(urgency: 2, at: t0), announcements: [], now: t0 + 1_999
            ),
            .suppressedByKey
        )
    }

    /// Strict `<`: exactly at the window the track speaks again.
    func testSameKeyIsFreeExactlyAtTheEndOfTheWindow() throws {
        let event = try Make.x4(trackId: "t", cls: .cyclist, bearingDeg: 170.0, rangeBand: .near)
        XCTAssertEqual(
            RefractoryGate.check(
                event, urgency: 2, record: track(urgency: 2, at: t0), announcements: [], now: t0 + 2_000
            ),
            .pass
        )
    }

    func testEscalationBeatsTheRefractoryWindow() throws {
        let event = try Make.x4(trackId: "t", cls: .cyclist, bearingDeg: 170.0, rangeBand: .near)
        XCTAssertEqual(
            RefractoryGate.check(
                event, urgency: 3, record: track(urgency: 2, at: t0), announcements: [], now: t0 + 100
            ),
            .pass,
            "urgency 3 has refractory 0 -- it always speaks"
        )
    }

    func testAwareTrackIsSuppressedForEightSeconds() throws {
        let event = try Make.x4(trackId: "t", cls: .pole, bearingDeg: 20.0, rangeBand: .near)
        XCTAssertEqual(
            RefractoryGate.check(
                event, urgency: 1, record: track(urgency: 1, at: t0), announcements: [], now: t0 + 7_999
            ),
            .suppressedByKey
        )
        XCTAssertEqual(
            RefractoryGate.check(
                event, urgency: 1, record: track(urgency: 1, at: t0), announcements: [], now: t0 + 8_000
            ),
            .pass
        )
    }

    func testRefractoryTableMatchesTheSpec() {
        XCTAssertEqual(RefractoryGate.refractoryMs(forUrgency: 3), 0)
        XCTAssertEqual(RefractoryGate.refractoryMs(forUrgency: 2), 2_000)
        XCTAssertEqual(RefractoryGate.refractoryMs(forUrgency: 1), 8_000)
    }

    // MARK: - Spatial refractory (A6), which replaces FAILOVER_SUPPRESS_MS

    /// A17: cloud announces a cyclist at 170; local reports the same object
    /// under a different track id at 172 inside the window -- one object,
    /// one announcement.
    func testSameClassNearbyFromAnotherKeyIsSuppressed() throws {
        let local = try Make.x4(source: .x4Local, trackId: "l1", cls: .cyclist, bearingDeg: 172.0, rangeBand: .near)
        let cloudSaidIt = announcement(source: .x4Cloud, trackId: "c1", bearingDeg: 170.0, urgency: 2, at: t0)
        XCTAssertEqual(
            RefractoryGate.check(local, urgency: 2, record: nil, announcements: [cloudSaidIt], now: t0 + 500),
            .suppressedSpatially
        )
    }

    func testSpatialSuppressionEndsWithTheWindow() throws {
        let local = try Make.x4(source: .x4Local, trackId: "l1", cls: .cyclist, bearingDeg: 172.0, rangeBand: .near)
        let cloudSaidIt = announcement(source: .x4Cloud, trackId: "c1", bearingDeg: 170.0, urgency: 2, at: t0)
        XCTAssertEqual(
            RefractoryGate.check(local, urgency: 2, record: nil, announcements: [cloudSaidIt], now: t0 + 2_000),
            .pass
        )
    }

    func testSpatialSuppressionNeedsTheSameClass() throws {
        let person = try Make.x4(source: .x4Local, trackId: "l2", cls: .person, bearingDeg: 172.0, rangeBand: .near)
        let cyclistSaid = announcement(source: .x4Cloud, trackId: "c1", cls: .cyclist, bearingDeg: 170.0, at: t0)
        XCTAssertEqual(
            RefractoryGate.check(person, urgency: 2, record: nil, announcements: [cyclistSaid], now: t0 + 500),
            .pass
        )
    }

    func testSpatialSuppressionNeedsProximity() throws {
        let far = try Make.x4(source: .x4Local, trackId: "l3", cls: .cyclist, bearingDeg: 150.0, rangeBand: .near)
        let said = announcement(source: .x4Cloud, trackId: "c1", bearingDeg: 170.0, at: t0)
        XCTAssertEqual(
            RefractoryGate.check(far, urgency: 2, record: nil, announcements: [said], now: t0 + 500),
            .pass
        )
    }

    // MARK: - The §4.5 handoff row (A6)

    func testX4ToPhoneHandoffIsSuppressedWithinThirtyDegrees() throws {
        let phone = try Make.phone(trackId: "p1", cls: .person, bearingDeg: 20.0, rangeM: 4.0)
        let x4Said = announcement(source: .x4Local, trackId: "x1", cls: .person, bearingDeg: 45.0, urgency: 2, at: t0)
        XCTAssertEqual(
            RefractoryGate.check(phone, urgency: 2, record: nil, announcements: [x4Said], now: t0 + 1_400),
            .suppressedSpatially,
            "the same pedestrian crossing into the phone's sector must not be announced twice"
        )
    }

    func testHandoffIsDirectional() throws {
        let x4 = try Make.x4(source: .x4Local, trackId: "x1", cls: .person, bearingDeg: 45.0, rangeBand: .near)
        let phoneSaid = announcement(source: .phone, trackId: "p1", cls: .person, bearingDeg: 20.0, urgency: 2, at: t0)
        XCTAssertEqual(
            RefractoryGate.check(x4, urgency: 2, record: nil, announcements: [phoneSaid], now: t0 + 1_400),
            .pass,
            "phone-to-X4 is not a handoff: the object left the collision zone"
        )
    }

    func testHandoffNeverSuppressesAnEscalation() throws {
        let phone = try Make.phone(trackId: "p1", cls: .person, bearingDeg: 20.0, rangeM: 1.0, closingMps: 2.0)
        let x4Said = announcement(source: .x4Local, trackId: "x1", cls: .person, bearingDeg: 45.0, urgency: 2, at: t0)
        XCTAssertEqual(
            RefractoryGate.check(phone, urgency: 3, record: nil, announcements: [x4Said], now: t0 + 500),
            .pass
        )
    }

    // MARK: - The urgency-3 repeat floor (A10)

    func testUrgencyThreeCannotRepeatWithinASecond() throws {
        let second = try Make.x4(source: .x4Local, trackId: "l9", cls: .cyclist, bearingDeg: 172.0, rangeBand: .near)
        let firstStop = announcement(source: .x4Cloud, trackId: "c9", bearingDeg: 170.0, urgency: 3, at: t0)
        XCTAssertEqual(
            RefractoryGate.check(second, urgency: 3, record: nil, announcements: [firstStop], now: t0 + 999),
            .suppressedSpatially
        )
        XCTAssertEqual(
            RefractoryGate.check(second, urgency: 3, record: nil, announcements: [firstStop], now: t0 + 1_000),
            .pass
        )
    }

    func testUrgencyThreeIsExemptFromEveryOtherWindow() throws {
        let event = try Make.x4(source: .x4Local, trackId: "l9", cls: .cyclist, bearingDeg: 172.0, rangeBand: .near)
        let awareNearby = announcement(source: .x4Cloud, trackId: "c9", bearingDeg: 170.0, urgency: 1, at: t0)
        XCTAssertEqual(
            RefractoryGate.check(event, urgency: 3, record: nil, announcements: [awareNearby], now: t0 + 10),
            .pass,
            "a whispered aware must never swallow a Stop"
        )
    }

    // MARK: - Bounded memory (A6, A15)

    func testRecentAnnouncementsAreBoundedAndAgeOut() {
        var records: [AnnouncementRecord] = []
        for index in 0..<(RecentAnnouncements.maxRecords * 3) {
            records = RecentAnnouncements.appending(
                announcement(trackId: "a\(index)", at: t0 + Int64(index)),
                to: records,
                now: t0 + Int64(index)
            )
        }
        XCTAssertLessThanOrEqual(records.count, RecentAnnouncements.maxRecords)
        XCTAssertTrue(
            RecentAnnouncements.pruned(
                records, now: t0 + RecentAnnouncements.retentionMs + Int64(RecentAnnouncements.maxRecords * 3)
            ).isEmpty
        )
    }
}
