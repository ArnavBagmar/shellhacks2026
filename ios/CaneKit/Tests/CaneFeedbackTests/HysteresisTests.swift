import CaneContracts
import XCTest

@testable import CaneFeedback

/// A9: "urgency thresholds use bands with hysteresis so a noisy estimate
/// cannot flip urgency back and forth". Anchored on the last *announced*
/// decision, so what the wearer heard is what the comparison is against.
final class HysteresisTests: XCTestCase {
    private let t0 = Make.t0

    private func record(
        urgency: Int,
        driver: DrivingMetric,
        presentedAtMs: Int64? = nil
    ) -> TrackRecord {
        let at = presentedAtMs ?? t0
        return TrackRecord(lastUrgency: urgency, lastDriver: driver, presentedAtMs: at, lastSeenMs: at)
    }

    func testNoRecordMeansNoHysteresis() {
        let raw = UrgencyDecision(urgency: 2, driver: .ttc(3.0))
        let result = Hysteresis.apply(raw, record: nil, now: t0)
        XCTAssertEqual(result.decision, raw)
        XCTAssertFalse(result.held)
    }

    func testUpgradesAreImmediate() {
        let raw = UrgencyDecision(urgency: 3, driver: .ttc(1.2))
        let result = Hysteresis.apply(raw, record: record(urgency: 1, driver: .band(.mid)), now: t0 + 10)
        XCTAssertEqual(result.decision.urgency, 3)
        XCTAssertFalse(result.held)
    }

    /// F3's row, verbatim: TTC 1.9 -> 3; then 2.2 -> still 3; 2.6 -> 2.
    func testTimeToContactDowngradeNeedsTheHalfSecondMargin() {
        let announced = record(urgency: 3, driver: .ttc(1.9))

        let wobble = Hysteresis.apply(UrgencyDecision(urgency: 2, driver: .ttc(2.2)), record: announced, now: t0 + 200)
        XCTAssertEqual(wobble.decision.urgency, 3, "2.2 s has not cleared 2.0 + 0.5")
        XCTAssertTrue(wobble.held)

        let cleared = Hysteresis.apply(UrgencyDecision(urgency: 2, driver: .ttc(2.6)), record: announced, now: t0 + 400)
        XCTAssertEqual(cleared.decision.urgency, 2)
        XCTAssertFalse(cleared.held)
    }

    func testStaticRangeDowngradeNeedsTheHalfMetreMargin() {
        let announced = record(urgency: 2, driver: .staticRange(1.9))
        let wobble = Hysteresis.apply(
            UrgencyDecision(urgency: 1, driver: .staticRange(2.2)), record: announced, now: t0 + 100
        )
        XCTAssertEqual(wobble.decision.urgency, 2)
        XCTAssertTrue(wobble.held)

        let cleared = Hysteresis.apply(
            UrgencyDecision(urgency: 1, driver: .staticRange(2.6)), record: announced, now: t0 + 200
        )
        XCTAssertEqual(cleared.decision.urgency, 1)
    }

    func testBandDowngradeNeedsAFullBandStep() {
        let announced = record(urgency: 3, driver: .band(.near))
        let sameBand = Hysteresis.apply(
            UrgencyDecision(urgency: 2, driver: .band(.near)), record: announced, now: t0 + 100
        )
        XCTAssertEqual(sameBand.decision.urgency, 3, "the closing estimate wobbled, the band did not")
        XCTAssertTrue(sameBand.held)

        let steppedOut = Hysteresis.apply(
            UrgencyDecision(urgency: 2, driver: .band(.mid)), record: announced, now: t0 + 100
        )
        XCTAssertEqual(steppedOut.decision.urgency, 2)
    }

    func testDriverKindChangeNeedsTheDwell() {
        let announced = record(urgency: 3, driver: .ttc(1.5))
        let early = Hysteresis.apply(
            UrgencyDecision(urgency: 1, driver: .band(.mid)), record: announced, now: t0 + 500
        )
        XCTAssertEqual(early.decision.urgency, 3)
        XCTAssertTrue(early.held)

        let dwelt = Hysteresis.apply(
            UrgencyDecision(urgency: 1, driver: .band(.mid)), record: announced, now: t0 + Hysteresis.dwellMs
        )
        XCTAssertEqual(dwelt.decision.urgency, 1)
    }

    /// The carve-out that stops the "announces late" failure class: an
    /// object that has passed you is never held at act-soon.
    func testHardZeroIsNeverHeld() {
        let announced = record(urgency: 3, driver: .ttc(1.5))
        let receded = UrgencyDecision(urgency: 0, driver: .band(.near), isHardZero: true)
        let result = Hysteresis.apply(receded, record: announced, now: t0 + 50)
        XCTAssertEqual(result.decision.urgency, 0)
        XCTAssertFalse(result.held)
    }

    /// Once the margin is cleared the level may fall more than one step.
    func testAcceptedDowngradeMayDropMultipleLevels() {
        let announced = record(urgency: 3, driver: .ttc(1.9))
        let result = Hysteresis.apply(UrgencyDecision(urgency: 0, driver: .ttc(9.0)), record: announced, now: t0 + 900)
        XCTAssertEqual(result.decision.urgency, 0)
    }

    /// Track records are bounded and age out (A15).
    func testTrackRecordsAreBoundedAndAgeOut() {
        var tracks: [FeedbackKey: TrackRecord] = [:]
        for index in 0..<(TrackRecords.maxRecords * 2) {
            let key = FeedbackKey(source: .x4Local, trackId: "bulk-\(index)")
            tracks = TrackRecords.recordingPresentation(
                tracks, key: key, urgency: 1, driver: .band(.mid), now: t0 + Int64(index)
            )
        }
        XCTAssertLessThanOrEqual(tracks.count, TrackRecords.maxRecords)

        let lastSeen = t0 + Int64(TrackRecords.maxRecords * 2) - 1
        let stale = TrackRecords.pruned(tracks, now: lastSeen + TrackRecords.ttlMs + 1)
        XCTAssertTrue(stale.isEmpty)
    }

    /// A sighting keeps a record alive without rewriting what was announced.
    func testSightingRefreshesLastSeenButNotTheAnnouncedLevel() {
        let key = FeedbackKey(source: .phone, trackId: "p1")
        var tracks = TrackRecords.recordingPresentation(
            [:], key: key, urgency: 2, driver: .ttc(3.0), now: t0
        )
        tracks = TrackRecords.touching(tracks, key: key, now: t0 + 5_000)
        XCTAssertEqual(tracks[key]?.lastUrgency, 2)
        XCTAssertEqual(tracks[key]?.presentedAtMs, t0)
        XCTAssertEqual(tracks[key]?.lastSeenMs, t0 + 5_000)
    }
}
