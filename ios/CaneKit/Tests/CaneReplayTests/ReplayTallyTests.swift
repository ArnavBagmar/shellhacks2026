import CaneContracts
import CaneFeedback
import XCTest

import CaneReplay

/// The tally is the instrument's read-out. If it is wrong, the milestone
/// reports fiction, so it is tested before anything uses it.
final class ReplayTallyTests: XCTestCase {
    private func announcement(
        _ level: AnnouncementLevel, cls: ObjectClass = .car, id: UInt64 = 1
    ) throws -> Announcement {
        Announcement(
            id: id,
            level: level,
            event: try Fixture.event(cls: cls),
            decidedAtMs: Fixture.emitMs
        )
    }

    func testAnEmptyTallyCountsNothing() {
        let tally = ReplayTally()

        XCTAssertEqual(tally.announcements, 0)
        XCTAssertEqual(tally.stops, 0)
        XCTAssertEqual(tally.spanMs, 0)
    }

    /// The house rule: accumulate by returning a new value, never by mutating.
    func testAddingDoesNotMutateTheOriginal() throws {
        let empty = ReplayTally()

        _ = empty.adding(try announcement(.immediate))

        XCTAssertEqual(empty.announcements, 0)
    }

    func testAnnouncementsAreCountedByUrgency() throws {
        let tally = ReplayTally()
            .adding(try announcement(.aware))
            .adding(try announcement(.actSoon))
            .adding(try announcement(.immediate))
            .adding(try announcement(.immediate))

        XCTAssertEqual(tally.announcements, 4)
        XCTAssertEqual(tally.count(urgency: 1), 1)
        XCTAssertEqual(tally.count(urgency: 2), 1)
        XCTAssertEqual(tally.count(urgency: 3), 2)
    }

    func testStopsAreTheUrgencyThreeCount() throws {
        let tally = ReplayTally()
            .adding(try announcement(.immediate))
            .adding(try announcement(.aware))

        XCTAssertEqual(tally.stops, 1)
    }

    /// Urgency 1 is haptic-only by design; 2 and 3 speak. The split is the
    /// "don't flood the ears" number.
    func testSpeechAndHapticOnlyAreSplit() throws {
        let tally = ReplayTally()
            .adding(try announcement(.aware))
            .adding(try announcement(.actSoon))
            .adding(try announcement(.immediate))

        XCTAssertEqual(tally.spoken, 2)
        XCTAssertEqual(tally.hapticOnly, 1)
    }

    func testAnnouncementsAreCountedByClass() throws {
        let tally = ReplayTally()
            .adding(try announcement(.immediate, cls: .car))
            .adding(try announcement(.actSoon, cls: .car))
            .adding(try announcement(.aware, cls: .person))

        XCTAssertEqual(tally.count(cls: .car), 2)
        XCTAssertEqual(tally.count(cls: .person), 1)
        XCTAssertEqual(tally.count(cls: .bus), 0)
    }

    func testSpanRunsFromTheFirstEmitStampToTheLast() {
        let tally = ReplayTally()
            .observing(emitMs: 1_000)
            .observing(emitMs: 61_000)
            .observing(emitMs: 31_000)

        XCTAssertEqual(tally.spanMs, 60_000)
    }

    func testPerMinuteUsesTheObservedSpan() throws {
        let tally = ReplayTally()
            .observing(emitMs: 0)
            .observing(emitMs: 120_000)
            .adding(try announcement(.immediate))
            .adding(try announcement(.immediate))
            .adding(try announcement(.immediate))
            .adding(try announcement(.immediate))

        XCTAssertEqual(tally.perMinute(tally.stops), 2.0, accuracy: 0.001)
    }

    /// A single-event recording has no span; the rate is 0, not a crash.
    func testPerMinuteIsZeroForAZeroSpan() throws {
        let tally = ReplayTally()
            .observing(emitMs: 500)
            .adding(try announcement(.immediate))

        XCTAssertEqual(tally.perMinute(tally.stops), 0.0)
    }
}
