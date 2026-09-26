import XCTest

@testable import CaneNavigation

/// The Routes API maneuver vocabulary (step-12 brief H1: 21 values) and
/// its mapping onto the direction a walker is told to take. A value the
/// API adds tomorrow must decode to `.unknown` rather than throw -- a new
/// enum case on Google's side must never stop a route from loading.
final class ManeuverTests: XCTestCase {
    private let documentedRawValues = [
        "MANEUVER_UNSPECIFIED", "TURN_SLIGHT_LEFT", "TURN_SHARP_LEFT", "UTURN_LEFT", "TURN_LEFT",
        "TURN_SLIGHT_RIGHT", "TURN_SHARP_RIGHT", "UTURN_RIGHT", "TURN_RIGHT", "STRAIGHT",
        "RAMP_LEFT", "RAMP_RIGHT", "MERGE", "FORK_LEFT", "FORK_RIGHT", "FERRY", "FERRY_TRAIN",
        "ROUNDABOUT_LEFT", "ROUNDABOUT_RIGHT", "DEPART", "NAME_CHANGE",
    ]

    func testAllTwentyOneDocumentedValuesDecode() throws {
        XCTAssertEqual(documentedRawValues.count, 21)
        for raw in documentedRawValues {
            let decoded = try JSONDecoder().decode(Maneuver.self, from: Data("\"\(raw)\"".utf8))
            XCTAssertEqual(decoded.rawValue, raw, "\(raw) did not round-trip")
            XCTAssertNotEqual(decoded, .unknown, "\(raw) should be a known maneuver")
        }
    }

    func testUnrecognisedValueDecodesToUnknownWithoutThrowing() throws {
        let decoded = try JSONDecoder().decode(Maneuver.self, from: Data("\"TELEPORT_LEFT\"".utf8))
        XCTAssertEqual(decoded, .unknown)
    }

    func testUnknownHasARawValueOutsideTheDocumentedVocabulary() {
        XCTAssertFalse(documentedRawValues.contains(Maneuver.unknown.rawValue))
    }

    func testTurnDirectionMapping() {
        let expected: [Maneuver: TurnDirection] = [
            .turnLeft: .left, .turnRight: .right,
            .turnSlightLeft: .slightLeft, .turnSlightRight: .slightRight,
            .turnSharpLeft: .sharpLeft, .turnSharpRight: .sharpRight,
            .uTurnLeft: .uTurn, .uTurnRight: .uTurn,
            .rampLeft: .left, .rampRight: .right,
            .forkLeft: .slightLeft, .forkRight: .slightRight,
            .roundaboutLeft: .left, .roundaboutRight: .right,
            .straight: .straight, .depart: .straight, .nameChange: .straight,
            .merge: .straight, .ferry: .straight, .ferryTrain: .straight,
            .unspecified: .straight, .unknown: .straight,
        ]

        for (maneuver, direction) in expected {
            XCTAssertEqual(maneuver.turnDirection, direction, "\(maneuver.rawValue)")
        }
        XCTAssertEqual(expected.count, Maneuver.allCases.count)
    }

    /// Only these earn a `.prepare`/`.now` cue; straight-ahead
    /// maneuvers stay silent (brief F3).
    func testOnlyDirectionChangesCountAsTurns() {
        let turning = Maneuver.allCases.filter(\.isTurn).map(\.rawValue).sorted()

        XCTAssertEqual(
            turning,
            [
                "FORK_LEFT", "FORK_RIGHT", "RAMP_LEFT", "RAMP_RIGHT", "ROUNDABOUT_LEFT",
                "ROUNDABOUT_RIGHT", "TURN_LEFT", "TURN_RIGHT", "TURN_SHARP_LEFT",
                "TURN_SHARP_RIGHT", "TURN_SLIGHT_LEFT", "TURN_SLIGHT_RIGHT", "UTURN_LEFT",
                "UTURN_RIGHT",
            ]
        )
    }
}
