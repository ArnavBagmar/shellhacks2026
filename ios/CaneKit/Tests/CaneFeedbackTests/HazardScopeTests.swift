import CaneContracts
import XCTest

@testable import CaneFeedback

/// "Only detect vehicles, and only if the car is starting to advance towards
/// you" (2026-09-26).
final class HazardScopeTests: XCTestCase {
    private let scope = HazardScope.approachingVehicles

    private func x4(_ cls: ObjectClass, closing: Double?, band: RangeBand = .near, track: String = "t1") throws
        -> AwarenessEvent {
        try Make.event(source: .x4Cloud, trackId: track, cls: cls, rangeBand: band, closingMps: closing)
    }

    func testAnApproachingCarOrBusIsAHazard() throws {
        XCTAssertTrue(scope.admits(try x4(.car, closing: 2.0)))
        XCTAssertTrue(scope.admits(try x4(.bus, closing: 4.5, band: .mid)))
        XCTAssertTrue(scope.admits(try x4(.car, closing: HazardScope.approachMps)))
    }

    func testACarThatIsNotComingAtYouIsNot() throws {
        XCTAssertFalse(scope.admits(try x4(.car, closing: 0.0)), "stopped")
        XCTAssertFalse(scope.admits(try x4(.car, closing: -3.0)), "driving away")
        XCTAssertFalse(scope.admits(try x4(.car, closing: nil)), "no motion measured yet")
        XCTAssertFalse(scope.admits(try x4(.car, closing: 1.2)), "the wearer walking up to a parked car")
        XCTAssertFalse(scope.admits(try x4(.parkedCar, closing: 3.0)))
    }

    func testNothingButVehiclesIsAHazard() throws {
        for cls in ObjectClass.allCases where !HazardScope.isVehicle(cls) {
            XCTAssertFalse(scope.admits(try x4(cls, closing: 5.0)), "\(cls)")
        }
    }

    func testEverythingKeepsTheOldBehaviour() throws {
        let person = try x4(.person, closing: 2.0)
        XCTAssertTrue(HazardScope.everything.admits(person))
        XCTAssertEqual(HazardScope.everything.decide(person), UrgencyPolicy.decide(person))
    }

    func testACarComingAtYouIsAlwaysTheTopAlarm() throws {
        // Crosswalk: even far away and only just moving, it is "Stop".
        XCTAssertEqual(scope.decide(try x4(.car, closing: 1.6, band: .far)).urgency, UrgencyLevel.immediate)
        XCTAssertEqual(scope.decide(try x4(.bus, closing: 6.0, band: .near)).urgency, UrgencyLevel.immediate)
        XCTAssertEqual(scope.decide(try x4(.person, closing: 4.0)).urgency, UrgencyLevel.ambient)
    }

    func testAShakyCarBoxIsDemotedOneLevelNotSilenced() throws {
        let shaky = try Make.event(source: .x4Cloud, cls: .car, confidence: 0.4, rangeBand: .near, closingMps: 3.0)
        XCTAssertEqual(scope.decide(shaky).urgency, UrgencyLevel.actSoon)
    }

    func testTheManagerAnnouncesOnlyTheApproachingCar() async throws {
        let sink = RecordingSink()
        let clock = TestClock(now: Make.t0)
        let subject = FeedbackManager(
            sink: sink, state: FeedbackState(hazardScope: .approachingVehicles), clock: clock.reader
        )

        await subject.ingest(try x4(.person, closing: 3.0, track: "p"), now: Make.t0)
        await subject.ingest(try x4(.car, closing: 0.0, track: "parked"), now: Make.t0)
        await subject.ingest(try x4(.car, closing: 4.0, track: "coming"), now: Make.t0)
        await sink.waitUntilPresenting(atLeast: 1)
        await subject.settle()

        XCTAssertEqual(sink.presented.map(\.cls), [.car])
        let counters = await subject.counters
        XCTAssertEqual(counters.outOfScope, 2)
    }
}
