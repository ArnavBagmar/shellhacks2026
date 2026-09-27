import XCTest

@testable import CaneNavigation

/// H-12b: navigation speech shares one synthesizer with hazard speech,
/// so it must obey the same direction-first word order (WCAG 3.2.4) and
/// stay short enough to be interruptible. Every phrase this module can
/// produce is checked, not just the ones the policy happens to emit.
final class PhrasingTests: XCTestCase {
    private func words(_ phrase: String) -> [String] {
        phrase.split(separator: " ").map(String.init)
    }

    func testEveryKindAndDirectionProducesAtMostSixWords() {
        for kind in CueKind.allCases {
            for direction in TurnDirection.allCases {
                for distance in [nil, 10, 30, 120] as [Int?] {
                    let phrase = Phrasing.phrase(kind: kind, direction: direction, distanceM: distance)
                    XCTAssertFalse(phrase.isEmpty, "\(kind) \(direction) has no phrase")
                    XCTAssertLessThanOrEqual(
                        words(phrase).count, 6, "\"\(phrase)\" is longer than six words"
                    )
                }
            }
        }
    }

    func testDirectionalKindsLeadWithTheDirectionWord() {
        for kind in CueKind.allCases where kind.usesDirectionWord {
            for direction in TurnDirection.allCases {
                let phrase = Phrasing.phrase(kind: kind, direction: direction, distanceM: 30)
                XCTAssertTrue(
                    phrase.hasPrefix(Phrasing.directionWord(direction)),
                    "\"\(phrase)\" does not start with \"\(Phrasing.directionWord(direction))\""
                )
            }
        }
    }

    func testCorrectionsSayWhatToDo() {
        XCTAssertEqual(Phrasing.phrase(kind: .correctHeading, direction: .slightLeft, distanceM: nil), "Bear left")
        XCTAssertEqual(Phrasing.phrase(kind: .correctHeading, direction: .right, distanceM: nil), "Turn right")
        XCTAssertEqual(Phrasing.phrase(kind: .correctHeading, direction: .uTurn, distanceM: nil), "Turn around")
    }

    /// The exact strings the brief (F4) writes down.
    func testDocumentedPhrases() {
        XCTAssertEqual(Phrasing.phrase(kind: .prepare, direction: .left, distanceM: 30), "Left in 30 metres")
        XCTAssertEqual(Phrasing.phrase(kind: .now, direction: .left, distanceM: nil), "Left now")
        XCTAssertEqual(
            Phrasing.phrase(kind: .prepare, direction: .right, distanceM: 30), "Right in 30 metres"
        )
        XCTAssertEqual(
            Phrasing.phrase(kind: .now, direction: .slightRight, distanceM: nil), "Slight right now"
        )
        XCTAssertEqual(
            Phrasing.phrase(kind: .continueStraight, direction: .straight, distanceM: nil), "Straight on"
        )
        XCTAssertEqual(Phrasing.phrase(kind: .now, direction: .uTurn, distanceM: nil), "Turn around now")
        XCTAssertEqual(
            Phrasing.phrase(kind: .offRoute, direction: .straight, distanceM: nil), "Off route, rerouting"
        )
        XCTAssertEqual(Phrasing.phrase(kind: .arrived, direction: .arrive, distanceM: nil), "Arrived")
        XCTAssertEqual(
            Phrasing.phrase(kind: .gpsLost, direction: .straight, distanceM: nil),
            "GPS lost, navigation paused"
        )
        XCTAssertEqual(
            Phrasing.phrase(kind: .gpsRestored, direction: .straight, distanceM: nil), "GPS back"
        )
    }

    func testDistancesRoundToTheNearestTenMetres() {
        XCTAssertEqual(Phrasing.roundedDistanceM(28.68), 30)
        XCTAssertEqual(Phrasing.roundedDistanceM(34.9), 30)
        XCTAssertEqual(Phrasing.roundedDistanceM(35.1), 40)
        XCTAssertEqual(Phrasing.roundedDistanceM(0), 0)
        // Never "in 0 metres": anything still ahead rounds up to 10.
        XCTAssertEqual(Phrasing.roundedDistanceM(3), 10)
    }

    // MARK: - The cue value

    func testCueDerivesItsPhraseAndStaysAtNavigationPriority() {
        let cue = TurnCue(kind: .prepare, direction: .left, distanceM: 30)

        XCTAssertEqual(cue.phrase, "Left in 30 metres")
        XCTAssertEqual(cue.priority, .navigation)
        XCTAssertEqual(cue.distanceM, 30)
    }

    /// H-12a: the hint is only a side; step 10 owns the pattern.
    func testHapticHintsAreSidesOnly() {
        XCTAssertEqual(TurnCue(kind: .now, direction: .left, distanceM: nil).hapticHint, .leftTurn)
        XCTAssertEqual(
            TurnCue(kind: .prepare, direction: .sharpRight, distanceM: 30).hapticHint, .rightTurn
        )
        XCTAssertEqual(
            TurnCue(kind: .correctHeading, direction: .right, distanceM: nil).hapticHint, .rightTurn
        )
        XCTAssertEqual(
            TurnCue(kind: .continueStraight, direction: .straight, distanceM: nil).hapticHint, .ahead
        )
        XCTAssertEqual(TurnCue(kind: .arrived, direction: .arrive, distanceM: nil).hapticHint, .none)
        XCTAssertEqual(TurnCue(kind: .gpsLost, direction: .straight, distanceM: nil).hapticHint, .none)
        XCTAssertEqual(TurnCue(kind: .now, direction: .uTurn, distanceM: nil).hapticHint, .none)
    }

    func testOnlyPrepareNowAndCorrectHeadingUseADirectionWord() {
        XCTAssertEqual(
            CueKind.allCases.filter(\.usesDirectionWord), [.prepare, .now]
        )
    }
}
