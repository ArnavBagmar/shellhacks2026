import Foundation

/// The nine directions a walker can be told to take. Shared by
/// `Maneuver` (what the route says happens at this point) and `TurnCue`
/// (what we say to the walker), so the mapping is written down exactly
/// once.
public enum TurnDirection: String, Sendable, Hashable, CaseIterable, Codable {
    case left
    case right
    case slightLeft
    case slightRight
    case sharpLeft
    case sharpRight
    case straight
    case uTurn
    case arrive

    /// Which way to correct when the walker's heading is off the path.
    ///
    /// This is the single definition of the heading-error sign for the
    /// whole module (brief F3): `headingErrorDeg` is
    /// `heading - segmentBearing` normalised into `(-180, 180]`, so a
    /// **positive** error means the walker faces clockwise of (i.e. to
    /// the right of) the path and must turn **left** to line up.
    public static func correction(forHeadingErrorDeg error: Double) -> TurnDirection {
        if error > 0 { return .left }
        if error < 0 { return .right }
        return .straight
    }

    /// True for the directions that involve leaving the current line of
    /// travel, i.e. the ones worth a haptic turn cue.
    public var isTurn: Bool {
        switch self {
        case .left, .right, .slightLeft, .slightRight, .sharpLeft, .sharpRight, .uTurn:
            return true
        case .straight, .arrive:
            return false
        }
    }
}
