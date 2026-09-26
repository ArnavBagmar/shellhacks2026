import CaneContracts

/// Named levels for the §4.7 urgency column. The wire never carries urgency
/// (it is an on-device policy), so these exist only inside CaneFeedback and
/// the `Announcement` it hands to step 10.
public enum UrgencyLevel {
    /// Log + hazard map, never announced.
    public static let ambient = 0
    /// Directional haptic only -- **never** speech.
    public static let aware = 1
    /// Directional haptic + a couple of words.
    public static let actSoon = 2
    /// Haptic burst + one word.
    public static let immediate = 3
}

/// Which measurement drove the level (A9). Hysteresis compares like with
/// like: a TTC that wobbled by 0.3 s is noise, a band that changed is not.
public enum DrivingMetric: Equatable, Sendable {
    /// Seconds of time-to-contact, from metric range and closing rate.
    case ttc(Double)
    /// Metres to a static obstacle.
    case staticRange(Double)
    /// No metric range at all -- the coarse band is all there is.
    case band(RangeBand)
}

/// The output of gate 5 (A9).
public struct UrgencyDecision: Equatable, Sendable {
    public let urgency: Int
    public let driver: DrivingMetric
    /// A9 carve-out: a zero produced by the receding rule or the
    /// behind-you rule is a fact about the world, not a noisy estimate, and
    /// hysteresis must never hold it. Carried on the decision so the
    /// hysteresis stage stays a pure function of its inputs.
    public let isHardZero: Bool

    public init(urgency: Int, driver: DrivingMetric, isHardZero: Bool = false) {
        self.urgency = urgency
        self.driver = driver
        self.isHardZero = isHardZero
    }

    /// Same level, same kind of driver -- used by the hysteresis comparison.
    public var driverKind: DriverKind {
        switch driver {
        case .ttc: return .ttc
        case .staticRange: return .staticRange
        case .band: return .band
        }
    }

    public enum DriverKind: Equatable, Sendable {
        case ttc
        case staticRange
        case band
    }
}
