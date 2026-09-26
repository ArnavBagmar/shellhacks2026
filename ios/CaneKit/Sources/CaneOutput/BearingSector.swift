import Foundation

/// The one file that turns a bearing into a side (step-10 design,
/// Amendment 6). Everything else -- phrases, haptic symbols, panning --
/// consumes `Sector`/`Side` and never re-derives from the sign.
///
/// Convention (docs/insta360feed.md, verbatim):
/// `bearing = yaw_view + (c_x - 0.5) * FOV`, **0 is straight ahead and
/// negative is the user's left**. Bearings are torso-relative, in
/// `(-180, 180]`.
public enum Side: Equatable, Sendable {
    case left, center, right

    public var mirrored: Side {
        switch self {
        case .left: return .right
        case .right: return .left
        case .center: return .center
        }
    }
}

public enum Sector: Equatable, Sendable, CaseIterable {
    case ahead, aheadLeft, aheadRight, left, right, behindLeft, behindRight, behind

    /// §C thresholds: |b| < 15 ahead; 15-60 ahead to a side; 60-120 to a
    /// side; 120-165 behind to a side; >= 165 directly behind.
    public init(bearingDeg: Double) {
        let wrapped = Self.normalize(bearingDeg)
        let magnitude = abs(wrapped)
        let isLeft = wrapped < 0
        switch magnitude {
        case ..<15: self = .ahead
        case ..<60: self = isLeft ? .aheadLeft : .aheadRight
        case ..<120: self = isLeft ? .left : .right
        case ..<165: self = isLeft ? .behindLeft : .behindRight
        default: self = .behind
        }
    }

    public var side: Side {
        switch self {
        case .aheadLeft, .left, .behindLeft: return .left
        case .aheadRight, .right, .behindRight: return .right
        case .ahead, .behind: return .center
        }
    }

    /// The five-band speech vocabulary, verbatim from §C.
    public var words: String {
        switch self {
        case .ahead: return "straight ahead"
        case .aheadLeft: return "ahead to the left"
        case .aheadRight: return "ahead to the right"
        case .left: return "to your left"
        case .right: return "to your right"
        case .behindLeft: return "behind you to the left"
        case .behindRight: return "behind you to the right"
        case .behind: return "directly behind you"
        }
    }

    /// Into `(-180, 180]`, so a caller passing 270 still gets "to your left".
    static func normalize(_ deg: Double) -> Double {
        guard deg.isFinite else { return 0 }
        var wrapped = deg.truncatingRemainder(dividingBy: 360)
        if wrapped <= -180 { wrapped += 360 }
        if wrapped > 180 { wrapped -= 360 }
        return wrapped
    }
}
