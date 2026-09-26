import Foundation

/// Which side of the body a hazard is on. `.center` is a real answer, not a
/// missing one: straight ahead and directly behind carry no left/right
/// information, and panning them to the middle is correct.
public enum Side: Equatable, Sendable {
    case left, right, center

    /// Swapping left and right must be total, so the mirror property test
    /// can run over every bearing without special-casing the poles.
    public var mirrored: Side {
        switch self {
        case .left: return .right
        case .right: return .left
        case .center: return .center
        }
    }
}

/// The five sectors the user is ever told about. Five, not nine: this matches
/// the Amendment 1 haptic alphabet one-for-one, so the buzz and the words can
/// never describe different things.
public enum Sector: Equatable, Sendable {
    /// Under 15 degrees.
    case ahead
    /// 15 up to 60 degrees.
    case aheadQuarter(Side)
    /// 60 up to 120 degrees.
    case lateral(Side)
    /// 120 up to and including 165 degrees.
    case behindQuarter(Side)
    /// Over 165 degrees.
    case behind

    public var side: Side {
        switch self {
        case .ahead, .behind:
            return .center
        case .aheadQuarter(let side), .lateral(let side), .behindQuarter(let side):
            return side
        }
    }
}

/// Amendment 6 -- the ONLY place a bearing becomes a side.
///
/// Convention, verbatim from step-10-brief §C:
/// `bearing = yaw_view + (c_x - 0.5) * FOV`, wrapped to [-180, 180).
/// **0 is straight ahead and negative is the user's left.**
///
/// A sign inversion here mirrors every announcement the product will ever
/// make and still passes every magnitude test, so `HapticScript` and
/// `StereoPan` consume `Sector`/`Side` from here rather than re-deriving
/// from the sign themselves. Two derivations could disagree, and a buzz on
/// the left with a voice saying "right" is worse than either alone.
public enum BearingSector {
    /// Boundary ownership is deliberate and tested: a magnitude of exactly
    /// 15, 60 or 120 belongs to the *wider* sector, and exactly 165 is still
    /// `behindQuarter` because the spec says "over 165" is directly behind.
    public static func sector(_ bearingDeg: Double) -> Sector {
        let magnitude = abs(bearingDeg)

        // Resolved once, here, and nowhere else in the whole output layer.
        let side: Side = bearingDeg < 0 ? .left : .right

        switch magnitude {
        case ..<15: return .ahead
        case ..<60: return .aheadQuarter(side)
        case ..<120: return .lateral(side)
        case ...165: return .behindQuarter(side)
        default: return .behind
        }
    }

    /// Direction first, always. Step 12's turn cues must match this word
    /// order (WCAG 3.2.4) or the user is learning two vocabularies for one
    /// device.
    public static func phrase(_ bearingDeg: Double) -> String {
        switch sector(bearingDeg) {
        case .ahead: return "straight ahead"
        case .aheadQuarter(.left): return "ahead to the left"
        case .aheadQuarter: return "ahead to the right"
        case .lateral(.left): return "to your left"
        case .lateral: return "to your right"
        case .behindQuarter(.left): return "behind you to the left"
        case .behindQuarter: return "behind you to the right"
        case .behind: return "directly behind you"
        }
    }
}
