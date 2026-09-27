import CaneContracts

public struct PathConflictResult: Equatable, Sendable {
    public let decision: UrgencyDecision
    /// True when this gate lowered the level, so the caller can count it.
    public let capped: Bool
}

/// Gate 5b: a Stop is for something on a collision course.
///
/// `UrgencyPolicy` escalates on radial closing speed, which a vehicle passing
/// three metres away satisfies completely — it is getting nearer right up to
/// the moment it is beside you. On a sidewalk beside traffic that is a Stop per
/// vehicle, and a Stop that means nothing teaches the wearer to ignore the one
/// that means everything. This gate asks the other half of the question: is the
/// bearing *holding* while the range closes, or sweeping past?
///
/// **Three deliberate limits on its power**, because a missed Stop is an injury
/// and a spurious one is only an annoyance:
///
/// 1. It only ever lowers urgency 3 to urgency 2 — which still speaks the
///    direction and the class ("ahead to the right, car fast"). Being wrong
///    costs the word "Stop", never the warning.
/// 2. It needs positive evidence of a sweep. No second sighting, no rate, no
///    cap: a first sighting keeps its Stop.
/// 3. It never touches a static class, which cannot be on a collision course in
///    the sense modelled here — you walk into a pole, it does not arrive.
public enum PathConflict {
    /// Above this corrected sweep, the object is going past rather than at you.
    ///
    /// Derivation. A car 3.5 m to the side at 13.4 m/s (30 mph) sweeps at
    /// `ω = v·d/r²`, which at the range where it first reads `near` (r ≈ 5 m)
    /// is ≈ 110 °/s. Walking sways the torso by ~10 °/s and a deliberate turn
    /// peaks near 90 °/s. 45 °/s sits clear of walking and well under the car;
    /// a fast deliberate turn is what `BearingRate.commonMode` removes.
    ///
    /// A demo knob, like `UrgencyPolicy.staticActM`. Milestone 1's recordings
    /// can re-tune it offline, with no second walk.
    public static let sweepThresholdDegPerS = 45.0

    public static func capped(
        _ decision: UrgencyDecision,
        event: AwarenessEvent,
        records: [AcceptedRecord]
    ) -> PathConflictResult {
        let unchanged = PathConflictResult(decision: decision, capped: false)

        guard decision.urgency == UrgencyLevel.immediate else { return unchanged }
        guard !UrgencyPolicy.isStatic(event.cls) else { return unchanged }
        guard let sweepDegPerS = BearingRate.corrected(FeedbackKey(event), in: records),
              abs(sweepDegPerS) > sweepThresholdDegPerS
        else { return unchanged }

        // Same measurement, one rung lower: the driver is untouched, exactly as
        // `UrgencyPolicy.confidenceGated` leaves it, so hysteresis still
        // compares like with like.
        return PathConflictResult(
            decision: UrgencyDecision(
                urgency: UrgencyLevel.actSoon,
                driver: decision.driver,
                isHardZero: decision.isHardZero
            ),
            capped: true
        )
    }
}
