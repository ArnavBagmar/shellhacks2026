import CaneTransport

/// Shepherd's stage 6, verbatim in shape: "GPS bias added, scaled by
/// `(1 - proximityFactor)` so obstacles win" (`docs/shepherd-steering-plan.md`
/// §0 row 6).
///
/// This changes the **steering** mix only. `OutputArbiter`'s speech priority is
/// untouched: hazard speech still preempts navigation speech, and a *spoken*
/// navigation cue arriving during a hazard announcement is still dropped
/// rather than queued (runbook "Demo decisions" 2, plan §4.3).
public enum SteerBlend {
    /// The target the integrator chases. Additive, then clamped to the
    /// contract's own steering range -- the wearer cannot be told to lean
    /// harder than "all the way", and `SteerLimits` is where that range lives.
    ///
    /// The blend happens *before* the integrator, matching Shepherd's stage
    /// order (6 then 8): the turn cue's contribution is smoothed by the same
    /// filter as the obstacle's, so a cue arriving mid-stride eases in instead
    /// of snapping the pan across.
    public static func lateralTarget(steer: Double, navigationBias: Double, proximityFactor: Double) -> Double {
        let weight = 1 - min(max(proximityFactor, 0), 1)
        let sum = steer + navigationBias * weight
        return min(max(sum, SteerLimits.steerMin), SteerLimits.steerMax)
    }
}

/// One navigation turn cue's pull on the steering mix, with an expiry.
///
/// Shepherd's bias is a continuous GPS heading error; ours is a discrete turn
/// cue ("Left now"), so it is held for `SteerPolicy.navigationBiasTtlMs` and
/// then stops counting. Same discipline as every other perishable number on
/// this phone: nothing is held past the point where it is still true.
public struct NavigationBias: Equatable, Sendable {
    /// -1 = hard left, +1 = hard right, matching `SteerCommand.steer` and
    /// `BearingSector`'s sign convention.
    public let value: Double
    public let expiresAtMs: Int64

    public init(lateral: Double, expiresAtMs: Int64) {
        value = min(max(lateral, SteerLimits.steerMin), SteerLimits.steerMax)
        self.expiresAtMs = expiresAtMs
    }

    public init(lateral: Double, now: Int64) {
        self.init(lateral: lateral, expiresAtMs: now + SteerPolicy.navigationBiasTtlMs)
    }

    /// Zero once expired. Strict `>`, exactly as `StalenessGate`: a bias at
    /// its expiry instant is still true.
    public func lateral(now: Int64) -> Double {
        StalenessGate.isExpired(expiresAtMs: expiresAtMs, now: now) ? 0 : value
    }
}
