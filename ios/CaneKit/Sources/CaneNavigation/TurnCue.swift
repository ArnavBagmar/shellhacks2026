import Foundation

/// What kind of thing navigation is telling the walker.
public enum CueKind: String, Sendable, Hashable, CaseIterable {
    /// "Left in 30 metres" -- a maneuver is coming up.
    case prepare
    /// "Left now" -- the maneuver is here.
    case now
    /// Reassurance after a heading correction.
    case continueStraight
    /// The walker is facing well off the path while moving.
    case correctHeading
    /// Cross-track has exceeded the off-route threshold for long
    /// enough to be real, not GPS noise.
    case offRoute
    /// A new route is being fetched.
    case rerouting
    case arrived
    case gpsLost
    case gpsRestored

    /// Kinds whose phrase leads with a direction word. The others say
    /// the same thing whatever `TurnCue.direction` holds.
    public var usesDirectionWord: Bool {
        switch self {
        case .prepare, .now:
            return true
        // A correction leads with what to do ("Bear left", "Turn around").
        case .correctHeading, .continueStraight, .offRoute, .rerouting, .arrived, .gpsLost, .gpsRestored:
            return false
        }
    }
}

/// Where navigation sits in the feedback queue.
///
/// Fixed at `.navigation`, which the integration must treat as **below
/// hazard urgency 1** (H-12b): while a hazard announcement is
/// presenting, or inside its 1800 ms speech gap, navigation speech is
/// dropped or delayed. Step 12 ships the value and this comment; the
/// arbitration itself belongs to the feedback manager.
public enum CuePriority: String, Sendable, Hashable {
    case navigation
}

/// A *hint* about which side a haptic should come from -- not a
/// pattern.
///
/// H-12a: step 12 must draw its turn cues from a disjoint region of the
/// haptic design space, so the step-10 owner maps these hints under
/// hard limits -- no script longer than **420 ms**, no intensity above
/// **0.85**, no continuous element longer than **400 ms**, and a
/// leading accent or gain tier distinct from the hazard `.left` /
/// `.right` alphabet, re-validated with the Amendment 7 protocol over
/// the combined alphabet. A turn cue that feels like the urgency-3
/// alarm is a wrong-action defect. `CaneNavigation` therefore never
/// constructs a `HapticScript`; it only says which side.
public enum HapticHint: String, Sendable, Hashable {
    case leftTurn
    case rightTurn
    case ahead
    /// No directional haptic: speech carries this cue (u-turns,
    /// arrival, GPS state).
    case none
}

/// One thing to say (and optionally buzz) to the walker. A value with
/// its phrase already rendered, so the integration never has to know
/// the phrasing rules.
public struct TurnCue: Sendable, Equatable {
    public let kind: CueKind
    public let direction: TurnDirection
    /// Rounded to the nearest 10 m; `nil` when the phrase carries no
    /// distance.
    public let distanceM: Int?
    public let phrase: String
    public let priority: CuePriority
    public let hapticHint: HapticHint

    public init(kind: CueKind, direction: TurnDirection, distanceM: Int?) {
        self.kind = kind
        self.direction = direction
        self.distanceM = distanceM
        self.phrase = Phrasing.phrase(kind: kind, direction: direction, distanceM: distanceM)
        self.priority = .navigation
        self.hapticHint = TurnCue.hint(kind: kind, direction: direction)
    }

    private static func hint(kind: CueKind, direction: TurnDirection) -> HapticHint {
        switch kind {
        case .prepare, .now, .correctHeading:
            return side(of: direction)
        case .continueStraight:
            return .ahead
        case .offRoute, .rerouting, .arrived, .gpsLost, .gpsRestored:
            return .none
        }
    }

    private static func side(of direction: TurnDirection) -> HapticHint {
        switch direction {
        case .left, .slightLeft, .sharpLeft:
            return .leftTurn
        case .right, .slightRight, .sharpRight:
            return .rightTurn
        case .straight:
            return .ahead
        case .uTurn, .arrive:
            // A u-turn has no single side, and arrival is not a
            // direction; step 10 gives the u-turn its own pattern.
            return .none
        }
    }
}
