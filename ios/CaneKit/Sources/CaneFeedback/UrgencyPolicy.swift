import CaneContracts

/// Gate 5 (§4.7.5, A7, A8). A *total* function: every class, with or
/// without metric range, with or without a closing rate, gets a level. The
/// class switch is exhaustive with no `default` (A15) -- a thirteenth class
/// must break this build rather than silently score 0.
///
/// `STATIC_ACT_M` is one of the three demo knobs (H6).
public enum UrgencyPolicy {
    /// Something at your back cannot be walked into.
    public static let staticRearDeg = 90.0
    /// Inside this, a static obstacle is an act-soon. Demo knob (H6).
    public static let staticActM = 2.0
    /// Inside this, it is worth knowing about.
    public static let staticAwareM = 5.0
    /// Below this the object is moving away, not towards you.
    public static let recedingMps = -0.5
    public static let ttcStopS = 2.0
    public static let ttcActS = 5.0
    public static let fastClosingMps = 3.0
    /// `near` <= 5 m; `mid` up to the trust horizon; `far` beyond it.
    public static let nearBandMaxM = 5.0

    /// A8 -- confidence gates the *output level*, not the detection.
    public static let minAnnounceConfidence = 0.25
    public static let minStopConfidence = 0.60
    public static let minSpeechConfidence = 0.50

    /// The whole gate: ladder, then confidence.
    public static func decide(_ event: AwarenessEvent) -> UrgencyDecision {
        confidenceGated(ladder(event), confidence: event.confidence)
    }

    /// `range_band` if the producer sent one, else derived from metric
    /// range, else `.far` -- the least urgent reading, never a crash.
    public static func effectiveBand(_ event: AwarenessEvent) -> RangeBand {
        if let band = event.rangeBand { return band }
        guard let metres = event.rangeM else { return .far }
        if metres <= nearBandMaxM { return .near }
        if metres <= RangeTrust.trustRangeM { return .mid }
        return .far
    }

    /// True for the classes that cannot move on their own; TTC never applies
    /// to them. Exhaustive, no `default`.
    public static func isStatic(_ cls: ObjectClass) -> Bool {
        switch cls {
        case .pole, .bollard, .wall, .parkedCar, .door, .overhang:
            return true
        case .person, .cyclist, .scooter, .car, .bus, .unknownObstacle:
            return false
        }
    }

    // MARK: - The ladder (A7)

    static func ladder(_ event: AwarenessEvent) -> UrgencyDecision {
        isStatic(event.cls) ? staticLadder(event) : movingLadder(event)
    }

    private static func staticLadder(_ event: AwarenessEvent) -> UrgencyDecision {
        let band = effectiveBand(event)
        guard AngularMath.absDelta(event.bearingDeg, 0.0) <= staticRearDeg else {
            return UrgencyDecision(urgency: UrgencyLevel.ambient, driver: .band(band), isHardZero: true)
        }
        if RangeTrust.isTrusted(event), let metres = event.rangeM {
            let level: Int
            if metres < staticActM {
                level = UrgencyLevel.actSoon
            } else if metres <= staticAwareM {
                level = UrgencyLevel.aware
            } else {
                level = UrgencyLevel.ambient
            }
            return UrgencyDecision(urgency: level, driver: .staticRange(metres))
        }
        let level = band == .near ? UrgencyLevel.aware : UrgencyLevel.ambient
        return UrgencyDecision(urgency: level, driver: .band(band))
    }

    private static func movingLadder(_ event: AwarenessEvent) -> UrgencyDecision {
        let band = effectiveBand(event)
        let closing = event.closingMps
        if let closing, closing < recedingMps {
            return UrgencyDecision(urgency: UrgencyLevel.ambient, driver: .band(band), isHardZero: true)
        }
        if let metres = event.rangeM, RangeTrust.isTrusted(event), let closing, closing > 0 {
            let ttc = metres / closing
            if ttc < ttcStopS { return UrgencyDecision(urgency: UrgencyLevel.immediate, driver: .ttc(ttc)) }
            if ttc <= ttcActS { return UrgencyDecision(urgency: UrgencyLevel.actSoon, driver: .ttc(ttc)) }
            // Beyond five seconds TTC says nothing useful: fall through.
        }
        let fast = (closing ?? 0) > fastClosingMps
        let approaching = (closing ?? 0) > 0
        let level: Int
        switch band {
        case .near:
            level = fast ? UrgencyLevel.immediate : (approaching ? UrgencyLevel.actSoon : UrgencyLevel.aware)
        case .mid:
            level = fast ? UrgencyLevel.actSoon : UrgencyLevel.aware
        case .far:
            level = fast ? UrgencyLevel.aware : UrgencyLevel.ambient
        }
        return UrgencyDecision(urgency: level, driver: .band(band))
    }

    // MARK: - Confidence gate (A8)

    /// Demotes by at most one level, and never below `aware` for a detection
    /// that is above the announce floor at all.
    static func confidenceGated(_ decision: UrgencyDecision, confidence: Double) -> UrgencyDecision {
        guard confidence >= minAnnounceConfidence else {
            return UrgencyDecision(
                urgency: UrgencyLevel.ambient,
                driver: decision.driver,
                isHardZero: decision.isHardZero
            )
        }
        let demoted: Int
        switch decision.urgency {
        case UrgencyLevel.immediate where confidence < minStopConfidence:
            demoted = UrgencyLevel.actSoon
        case UrgencyLevel.actSoon where confidence < minSpeechConfidence:
            demoted = UrgencyLevel.aware
        default:
            demoted = decision.urgency
        }
        return UrgencyDecision(urgency: demoted, driver: decision.driver, isHardZero: decision.isHardZero)
    }
}
