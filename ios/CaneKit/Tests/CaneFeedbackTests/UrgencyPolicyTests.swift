import CaneContracts
import XCTest

@testable import CaneFeedback

/// Gate 5 (A7, A8): urgency is computed on-device and never travels on the
/// wire. A7 makes it a *total* function -- every one of the twelve classes,
/// with or without metric range, with or without a closing rate, scores.
final class UrgencyPolicyTests: XCTestCase {
    private func urgency(_ event: AwarenessEvent) -> Int {
        UrgencyPolicy.decide(event).urgency
    }

    // MARK: - Static classes (A7, first block)

    func testStaticObstacleBehindYouScoresZero() throws {
        let pole = try Make.phone(cls: .pole, bearingDeg: 170.0, rangeM: 1.0)
        let decision = UrgencyPolicy.decide(pole)
        XCTAssertEqual(decision.urgency, 0)
        XCTAssertTrue(decision.isHardZero, "you cannot walk into something behind you")
    }

    func testStaticObstacleInsideTwoMetresIsActSoon() throws {
        let pole = try Make.phone(cls: .pole, bearingDeg: 5.0, rangeM: 1.9)
        XCTAssertEqual(UrgencyPolicy.decide(pole), UrgencyDecision(urgency: 2, driver: .staticRange(1.9)))
    }

    func testStaticObstacleInsideFiveMetresIsAware() throws {
        let pole = try Make.phone(cls: .pole, bearingDeg: 5.0, rangeM: 4.0)
        XCTAssertEqual(UrgencyPolicy.decide(pole), UrgencyDecision(urgency: 1, driver: .staticRange(4.0)))
    }

    func testStaticObstacleBeyondFiveMetresIsAmbient() throws {
        let pole = try Make.phone(cls: .pole, bearingDeg: 5.0, rangeM: 6.0)
        XCTAssertEqual(urgency(pole), 0)
    }

    func testStaticObstacleWithBandsOnlyIsAwareWhenNear() throws {
        let nearPole = try Make.x4(cls: .pole, bearingDeg: 20.0, rangeBand: .near)
        let midPole = try Make.x4(cls: .pole, bearingDeg: 20.0, rangeBand: .mid)
        XCTAssertEqual(UrgencyPolicy.decide(nearPole), UrgencyDecision(urgency: 1, driver: .band(.near)))
        XCTAssertEqual(urgency(midPole), 0)
    }

    /// The A7 escalation check, as a ladder.
    func testStaticPoleLadder() throws {
        XCTAssertEqual(urgency(try Make.phone(cls: .pole, rangeM: 6.0)), 0)
        XCTAssertEqual(urgency(try Make.phone(cls: .pole, rangeM: 4.0)), 1)
        XCTAssertEqual(urgency(try Make.phone(cls: .pole, rangeM: 1.9)), 2)
    }

    // MARK: - Moving classes (A7, second block)

    func testRecedingObjectScoresZeroWhateverTheRange() throws {
        let passed = try Make.phone(cls: .cyclist, rangeM: 2.0, closingMps: -1.0)
        let decision = UrgencyPolicy.decide(passed)
        XCTAssertEqual(decision.urgency, 0)
        XCTAssertTrue(decision.isHardZero, "a receding object is a fact, not a noisy estimate")
    }

    func testTimeToContactUnderTwoSecondsIsImmediate() throws {
        let cyclist = try Make.phone(cls: .cyclist, rangeM: 3.0, closingMps: 2.0)
        XCTAssertEqual(UrgencyPolicy.decide(cyclist), UrgencyDecision(urgency: 3, driver: .ttc(1.5)))
    }

    func testTimeToContactBetweenTwoAndFiveSecondsIsActSoon() throws {
        let cyclist = try Make.phone(cls: .cyclist, rangeM: 8.0, closingMps: 2.0)
        XCTAssertEqual(UrgencyPolicy.decide(cyclist), UrgencyDecision(urgency: 2, driver: .ttc(4.0)))
    }

    func testTimeToContactOverFiveSecondsFallsThroughToTheBandLadder() throws {
        let cyclist = try Make.phone(cls: .cyclist, rangeM: 14.0, closingMps: 1.0)
        XCTAssertEqual(UrgencyPolicy.decide(cyclist), UrgencyDecision(urgency: 1, driver: .band(.mid)))
    }

    func testBandLadderNear() throws {
        let fast = try Make.x4(cls: .cyclist, rangeBand: .near, closingMps: 4.0)
        let approaching = try Make.x4(cls: .cyclist, rangeBand: .near, closingMps: 1.0)
        let stationary = try Make.x4(cls: .cyclist, rangeBand: .near, closingMps: nil)
        XCTAssertEqual(urgency(fast), 3)
        XCTAssertEqual(urgency(approaching), 2)
        XCTAssertEqual(urgency(stationary), 1)
    }

    func testBandLadderMid() throws {
        XCTAssertEqual(urgency(try Make.x4(cls: .cyclist, rangeBand: .mid, closingMps: 4.0)), 2)
        XCTAssertEqual(urgency(try Make.x4(cls: .cyclist, rangeBand: .mid, closingMps: 1.0)), 1)
    }

    func testBandLadderFar() throws {
        XCTAssertEqual(urgency(try Make.x4(cls: .cyclist, rangeBand: .far, closingMps: 4.0)), 1)
        XCTAssertEqual(urgency(try Make.x4(cls: .cyclist, rangeBand: .far, closingMps: 1.0)), 0)
    }

    /// The A7 escalation check for an approaching cyclist the X4 owns.
    func testCyclistApproachLadderOnBandsAlone() throws {
        XCTAssertEqual(urgency(try Make.x4(cls: .cyclist, rangeBand: .far, closingMps: 6.0)), 1)
        XCTAssertEqual(urgency(try Make.x4(cls: .cyclist, rangeBand: .mid, closingMps: 6.0)), 2)
        XCTAssertEqual(urgency(try Make.x4(cls: .cyclist, rangeBand: .near, closingMps: 6.0)), 3)
        XCTAssertEqual(urgency(try Make.x4(cls: .cyclist, rangeBand: .near, closingMps: -6.0)), 0)
    }

    func testUnknownObstacleUsesTheMovingLadder() throws {
        XCTAssertEqual(urgency(try Make.x4(cls: .unknownObstacle, rangeBand: .near, closingMps: 4.0)), 3)
        XCTAssertEqual(urgency(try Make.phone(cls: .unknownObstacle, rangeM: 3.0, closingMps: 2.0)), 3)
    }

    // MARK: - Totality (A7, A15)

    /// A17: both range fields nil must not force-unwrap, and must be the
    /// least urgent reading.
    func testBothRangeFieldsNilIsTreatedAsFar() throws {
        let event = try Make.event(cls: .person, rangeM: nil, rangeBand: nil, closingMps: nil)
        XCTAssertEqual(UrgencyPolicy.effectiveBand(event), .far)
        XCTAssertEqual(urgency(event), 0)
        let fastUnknownBand = try Make.event(cls: .person, rangeM: nil, rangeBand: nil, closingMps: 5.0)
        XCTAssertEqual(urgency(fastUnknownBand), 1)
    }

    func testEveryClassScoresWithoutTrapping() throws {
        for cls in ObjectClass.allCases {
            let bandsOnly = try Make.x4(cls: cls, bearingDeg: 10.0, rangeBand: .near, closingMps: 1.0)
            XCTAssertTrue((0...3).contains(urgency(bandsOnly)), "\(cls.rawValue)")
            let metric = try Make.phone(cls: cls, bearingDeg: 10.0, rangeM: 3.0, closingMps: 1.0)
            XCTAssertTrue((0...3).contains(urgency(metric)), "\(cls.rawValue)")
        }
    }

    /// A17 monotonicity property: for a fixed class and closing rate, urgency
    /// never falls as the object gets closer.
    func testUrgencyIsMonotonicAsRangeCloses() throws {
        let ranges: [Double] = [20, 16, 15, 12, 10, 8, 7.5, 6, 5, 4, 3, 2.5, 2, 1.5, 1]
        for cls in ObjectClass.allCases {
            var previous = -1
            for range in ranges {
                let event = try Make.phone(
                    cls: cls, confidence: 0.95, bearingDeg: 0.0, rangeM: range, closingMps: 1.5
                )
                let scored = urgency(event)
                XCTAssertGreaterThanOrEqual(
                    scored, previous,
                    "\(cls.rawValue) dropped urgency while closing, at \(range) m"
                )
                previous = scored
            }
        }
    }

    // MARK: - Confidence gating (A8)

    func testLowConfidenceBelowTheAnnounceFloorScoresZero() throws {
        let event = try Make.phone(cls: .cyclist, confidence: 0.2, rangeM: 3.0, closingMps: 2.0)
        XCTAssertEqual(urgency(event), 0)
    }

    func testImmediateWithoutStopConfidenceDemotesOneLevel() throws {
        let event = try Make.phone(cls: .cyclist, confidence: 0.5, rangeM: 3.0, closingMps: 2.0)
        XCTAssertEqual(urgency(event), 2, "a 3 demotes to 2, never straight to 1")
    }

    func testActSoonWithoutSpeechConfidenceDemotesToHapticOnly() throws {
        let event = try Make.phone(cls: .cyclist, confidence: 0.45, rangeM: 8.0, closingMps: 2.0)
        XCTAssertEqual(urgency(event), 1)
    }

    func testAwareIsNeverDemotedBelowOneByConfidence() throws {
        let event = try Make.phone(cls: .pole, confidence: 0.3, rangeM: 4.0)
        XCTAssertEqual(urgency(event), 1)
    }

    func testConfidentImmediateIsNotDemoted() throws {
        let event = try Make.phone(cls: .cyclist, confidence: 0.9, rangeM: 3.0, closingMps: 2.0)
        XCTAssertEqual(urgency(event), 3)
    }
}
