import Foundation

/// Every phrase navigation can speak (step-12 brief F4).
///
/// Two rules, both from H-12b: **direction first** (WCAG 3.2.4 -- the
/// walker learns one word order for hazards and for navigation) and
/// **at most six words**, so a cue can be spoken inside the gap between
/// hazard announcements. `PhrasingTests` checks every `CueKind` x
/// `TurnDirection` combination, including the ones the policy never
/// emits, so the rule cannot be broken by a later change.
public enum Phrasing {
    /// Distances are spoken to the nearest 10 m; a walker cannot act on
    /// "in 27 metres".
    public static let distanceRoundingM = 10

    public static func phrase(kind: CueKind, direction: TurnDirection, distanceM: Int?) -> String {
        switch kind {
        case .prepare:
            guard let distanceM, distanceM > 0 else { return "\(directionWord(direction)) ahead" }
            return "\(directionWord(direction)) in \(distanceM) metres"
        case .now:
            return "\(directionWord(direction)) now"
        case .correctHeading:
            return "\(directionWord(direction)) to rejoin route"
        case .continueStraight:
            return "Straight on"
        case .offRoute:
            return "Off route, rerouting"
        case .rerouting:
            return "Rerouting"
        case .arrived:
            return "Arrived"
        case .gpsLost:
            return "GPS lost, navigation paused"
        case .gpsRestored:
            return "GPS back"
        }
    }

    /// The word (or two) a directional phrase opens with.
    public static func directionWord(_ direction: TurnDirection) -> String {
        switch direction {
        case .left: return "Left"
        case .right: return "Right"
        case .slightLeft: return "Slight left"
        case .slightRight: return "Slight right"
        case .sharpLeft: return "Sharp left"
        case .sharpRight: return "Sharp right"
        case .straight: return "Straight"
        case .uTurn: return "Turn around"
        case .arrive: return "Arrived"
        }
    }

    /// Rounds to the nearest 10 m, never down to zero: something still
    /// ahead is "in 10 metres", not "in 0 metres".
    public static func roundedDistanceM(_ meters: Double) -> Int {
        guard meters > 0 else { return 0 }
        let rounding = Double(distanceRoundingM)
        return max(distanceRoundingM, Int((meters / rounding).rounded()) * distanceRoundingM)
    }
}
