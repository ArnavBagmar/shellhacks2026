import Foundation

/// The complete Routes API `Maneuver` vocabulary (step-12 brief H1 --
/// 21 documented values), plus `.unknown` for anything Google adds
/// later.
///
/// `init(from:)` is custom precisely so an unrecognised string decodes to
/// `.unknown` instead of throwing: a new value on the server must never
/// stop a walking route from loading. `.unknown`'s raw value is
/// deliberately outside the documented vocabulary so it cannot collide.
public enum Maneuver: String, Codable, Sendable, Hashable, CaseIterable {
    case unspecified = "MANEUVER_UNSPECIFIED"
    case turnSlightLeft = "TURN_SLIGHT_LEFT"
    case turnSharpLeft = "TURN_SHARP_LEFT"
    case uTurnLeft = "UTURN_LEFT"
    case turnLeft = "TURN_LEFT"
    case turnSlightRight = "TURN_SLIGHT_RIGHT"
    case turnSharpRight = "TURN_SHARP_RIGHT"
    case uTurnRight = "UTURN_RIGHT"
    case turnRight = "TURN_RIGHT"
    case straight = "STRAIGHT"
    case rampLeft = "RAMP_LEFT"
    case rampRight = "RAMP_RIGHT"
    case merge = "MERGE"
    case forkLeft = "FORK_LEFT"
    case forkRight = "FORK_RIGHT"
    case ferry = "FERRY"
    case ferryTrain = "FERRY_TRAIN"
    case roundaboutLeft = "ROUNDABOUT_LEFT"
    case roundaboutRight = "ROUNDABOUT_RIGHT"
    case depart = "DEPART"
    case nameChange = "NAME_CHANGE"
    /// Not a Routes API value: whatever the API sent that this build
    /// does not know about.
    case unknown = "UNKNOWN_MANEUVER"

    public init(from decoder: any Decoder) throws {
        let raw = try decoder.singleValueContainer().decode(String.self)
        self = Maneuver(rawValue: raw) ?? .unknown
    }

    /// The direction a walker is asked to take at this maneuver.
    /// Everything that is not a change of direction maps to `.straight`.
    public var turnDirection: TurnDirection {
        switch self {
        case .turnLeft, .rampLeft, .roundaboutLeft:
            return .left
        case .turnRight, .rampRight, .roundaboutRight:
            return .right
        case .turnSlightLeft, .forkLeft:
            return .slightLeft
        case .turnSlightRight, .forkRight:
            return .slightRight
        case .turnSharpLeft:
            return .sharpLeft
        case .turnSharpRight:
            return .sharpRight
        case .uTurnLeft, .uTurnRight:
            return .uTurn
        case .straight, .depart, .nameChange, .merge, .ferry, .ferryTrain, .unspecified, .unknown:
            return .straight
        }
    }

    /// Whether this maneuver earns a `.prepare`/`.now` cue (brief F3:
    /// "Straight/continue maneuvers emit no prepare/now").
    public var isTurn: Bool { turnDirection.isTurn }
}
