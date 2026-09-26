import CaneContracts

/// Gate 6 (§4.7.4, A6, A10). "This is the anti-flooding mechanism, and it
/// is why track_id is in the contract" -- widened by A6 from one key to one
/// *object*, however it happens to be keyed, because a failover, a crop seam
/// and the §4.5 sector handoff all re-key the same hazard.
///
/// `SPATIAL_REFRACTORY_DEG` is one of the three demo knobs (H6).
public enum RefractoryGate {
    /// Urgency 3 always speaks (§4.7's refractory column).
    public static let refractoryImmediateMs: Int64 = 0
    public static let refractoryActSoonMs: Int64 = 2_000
    public static let refractoryAwareMs: Int64 = 8_000
    /// Same class, this close, is the same object under another name.
    public static let spatialRefractoryDeg = 10.0
    /// The X4-to-phone handoff is wider: the two cameras disagree about
    /// bearing by up to the calibration error plus the sector overlap.
    public static let handoffWindowMs: Int64 = 1_500
    public static let handoffDeg = 30.0
    /// Even a Stop does not repeat faster than this.
    public static let urgency3RepeatMs: Int64 = 1_000

    public enum Outcome: Equatable, Sendable {
        case pass
        /// This `(source, track_id)` spoke recently (`refractorySuppressed`).
        case suppressedByKey
        /// Another key said the same thing at the same bearing
        /// (`spatialSuppressed`).
        case suppressedSpatially
    }

    public static func refractoryMs(forUrgency urgency: Int) -> Int64 {
        switch urgency {
        case UrgencyLevel.immediate: return refractoryImmediateMs
        case UrgencyLevel.actSoon: return refractoryActSoonMs
        case UrgencyLevel.aware: return refractoryAwareMs
        default: return 0
        }
    }

    public static func check(
        _ event: AwarenessEvent,
        urgency: Int,
        record: TrackRecord?,
        announcements: [AnnouncementRecord],
        now: Int64
    ) -> Outcome {
        guard urgency != UrgencyLevel.immediate else {
            return immediateRepeatFloor(event, record: record, announcements: announcements, now: now)
        }
        if let record,
           urgency <= record.lastUrgency,
           now - record.presentedAtMs < refractoryMs(forUrgency: record.lastUrgency) {
            return .suppressedByKey
        }
        let key = FeedbackKey(event)
        for said in announcements where said.cls == event.cls && said.key != key {
            guard urgency <= said.urgency else { continue }
            let age = now - said.presentedAtMs
            if age < refractoryMs(forUrgency: said.urgency),
               AngularMath.isWithin(spatialRefractoryDeg, said.bearingDeg, event.bearingDeg) {
                return .suppressedSpatially
            }
            if isHandoff(from: said.key.source, to: event.source),
               age < handoffWindowMs,
               AngularMath.isWithin(handoffDeg, said.bearingDeg, event.bearingDeg) {
                return .suppressedSpatially
            }
        }
        return .pass
    }

    /// Urgency 3 is exempt from every duration window except its own repeat
    /// floor, and the floor only applies to *re-presenting* a Stop: a Stop
    /// is never swallowed by a quieter announcement (that would be the
    /// "silent on urgency 3" failure class).
    private static func immediateRepeatFloor(
        _ event: AwarenessEvent,
        record: TrackRecord?,
        announcements: [AnnouncementRecord],
        now: Int64
    ) -> Outcome {
        if let record,
           record.lastUrgency == UrgencyLevel.immediate,
           now - record.presentedAtMs < urgency3RepeatMs {
            return .suppressedByKey
        }
        let key = FeedbackKey(event)
        let repeated = announcements.contains { said in
            said.key != key
                && said.cls == event.cls
                && said.urgency == UrgencyLevel.immediate
                && now - said.presentedAtMs < urgency3RepeatMs
                && AngularMath.isWithin(spatialRefractoryDeg, said.bearingDeg, event.bearingDeg)
        }
        return repeated ? .suppressedSpatially : .pass
    }

    /// Directional: the §4.5 handoff is an object crossing *into* the
    /// phone's collision zone from the X4's wider view. The reverse
    /// (leaving the zone) is not a duplicate.
    private static func isHandoff(from announced: Source, to incoming: Source) -> Bool {
        guard incoming == .phone else { return false }
        switch announced {
        case .x4Local, .x4Cloud: return true
        case .phone: return false
        }
    }
}
