import CaneContracts
import XCTest

@testable import CaneFeedback

/// A12: the announcement is *structured*. Step 9 never builds a string and
/// never chooses a word -- "Stop" is wrong for an overtaking cyclist at 178
/// degrees (H2), and that table belongs to step 10.
final class AnnouncementTests: XCTestCase {
    private func announcement(
        level: AnnouncementLevel,
        event: AwarenessEvent,
        id: UInt64 = 1,
        decidedAtMs: Int64 = Make.t0
    ) -> Announcement {
        Announcement(id: id, level: level, event: event, decidedAtMs: decidedAtMs)
    }

    /// The acceptance criterion from the runbook: urgency 1 produces haptic
    /// only and never speech. It is unrepresentable, not merely untested.
    func testAwareIsHapticOnlyAndNeverSpeaks() throws {
        let event = try Make.phone(cls: .pole, bearingDeg: 12.0, rangeM: 4.0)
        let made = announcement(level: .aware, event: event)
        XCTAssertEqual(made.urgency, 1)
        XCTAssertNil(made.speech)
        XCTAssertEqual(made.haptic, .directional)
    }

    func testActSoonIsDirectionalHapticPlusBriefSpeech() throws {
        let event = try Make.phone(cls: .pole, bearingDeg: 12.0, rangeM: 1.5)
        let made = announcement(level: .actSoon, event: event)
        XCTAssertEqual(made.urgency, 2)
        XCTAssertEqual(made.haptic, .directional)
        XCTAssertEqual(made.speech, .brief)
    }

    func testImmediateIsABurstPlusOneWord() throws {
        let event = try Make.phone(cls: .cyclist, bearingDeg: 0.0, rangeM: 2.0, closingMps: 3.0)
        let made = announcement(level: .immediate, event: event)
        XCTAssertEqual(made.urgency, 3)
        XCTAssertEqual(made.haptic, .burst)
        XCTAssertEqual(made.speech, .oneWord)
    }

    func testMetricRangeBecomesRoundedWholeMetres() throws {
        let event = try Make.phone(cls: .pole, rangeM: 2.4)
        XCTAssertEqual(announcement(level: .actSoon, event: event).range, .aboutMetres(2))
        let further = try Make.phone(cls: .pole, rangeM: 2.6)
        XCTAssertEqual(announcement(level: .actSoon, event: further).range, .aboutMetres(3))
    }

    func testBandOnlyEventCarriesTheBand() throws {
        let event = try Make.x4(cls: .cyclist, rangeBand: .near, closingMps: 4.0)
        XCTAssertEqual(announcement(level: .immediate, event: event).range, .band(.near))
    }

    func testNoRangeInformationAtAllIsUnknown() throws {
        let event = try Make.event(cls: .person, rangeM: nil, rangeBand: nil, closingMps: 1.0)
        XCTAssertEqual(announcement(level: .aware, event: event).range, .unknown)
    }

    /// `decidedAtMs` is the §4.9 latency probe and `expiresAtMs` is what the
    /// dequeue re-gate and step 10 both read.
    func testTimestampsCarryTheLatencyAndExpiryFacts() throws {
        let event = try Make.x4(tCaptureMs: Make.t0, cls: .cyclist, rangeBand: .near, closingMps: 4.0)
        let made = announcement(level: .immediate, event: event, decidedAtMs: Make.t0 + 120)
        XCTAssertEqual(made.tCaptureMs, Make.t0)
        XCTAssertEqual(made.decidedAtMs, Make.t0 + 120)
        XCTAssertEqual(made.expiresAtMs, Make.t0 + 500, "cyclist TTL, clamped")
        XCTAssertEqual(made.key, FeedbackKey(event))
    }
}
