import Foundation

/// Shepherd's stage 8 as one number and two pure functions -- their
/// "boat-like momentum model" (`docs/shepherd-steering-plan.md` §0 row 8).
///
/// A value type with no clock and no actor: the tick passes elapsed time in,
/// so every timing property is arithmetic and every test is synchronous. The
/// actor (`FeedbackManager`) is the only thing that holds one of these.
public struct LeakyIntegrator: Equatable, Sendable {
    public var value: Double

    public init(value: Double = 0) {
        self.value = value
    }

    /// `out += (target - out) * k`, with `k` corrected for how much time
    /// actually passed: `k_eff = 1 - (1 - k)^(elapsed / tick)`.
    ///
    /// The exponential form, not `k * elapsed / tick`, for two reasons. It is
    /// the exact continuous-time equivalent, so the response depends on
    /// elapsed time and not on how often the loop happened to run -- a
    /// stuttering main thread must not retune the filter. And `k_eff` is
    /// **structurally** in `[0, 1)` for any non-negative elapsed time, which
    /// is what makes overshoot impossible rather than merely clamped: with
    /// the linear form, one long tick (the app was backgrounded) would give
    /// `k_eff > 1` and ring straight past the target.
    public func advanced(toward target: Double, elapsedMs: Int64) -> LeakyIntegrator {
        LeakyIntegrator(value: value + (target - value) * Self.gain(elapsedMs: elapsedMs))
    }

    /// Shepherd's stage 9: decay to neutral over `FAILSAFE_DECAY_MS`.
    ///
    /// A fixed *rate* (full scale per `failsafeDecayMs`) rather than a
    /// fraction of the current value: an exponential decay never actually
    /// reaches zero, and "the cue stopped" has to be a fact at a known
    /// instant. Because the value is bounded by full scale, this reaches
    /// neutral in at most the failsafe window from any starting point, however
    /// the interval is chopped up, and it can never cross zero to the far
    /// side.
    public func decayed(elapsedMs: Int64) -> LeakyIntegrator {
        let step = Double(max(0, elapsedMs)) / Double(SteerPolicy.failsafeDecayMs)
        let magnitude = max(0, abs(value) - step)
        // `0 * -1` is `-0.0`, which is `== 0` but prints as "-0.0" in a
        // failure message and in a log line; normalise it away.
        guard magnitude > 0 else { return LeakyIntegrator(value: 0) }
        return LeakyIntegrator(value: magnitude * (value < 0 ? -1 : 1))
    }

    public var isNeutral: Bool { value == 0 }

    /// `k` for an interval. Clamped at zero elapsed time so a clock that
    /// steps backwards (an NTP correction mid-walk) cannot run the filter in
    /// reverse.
    public static func gain(elapsedMs: Int64) -> Double {
        guard elapsedMs > 0 else { return 0 }
        let ticks = Double(elapsedMs) / Double(SteerPolicy.integratorTickMs)
        return 1 - pow(1 - SteerPolicy.integratorK, ticks)
    }

    /// Time for a step change to reach `fraction` of its target, the figure
    /// the brief quotes. Inverse of `gain`.
    public static func msToReach(fraction: Double) -> Double {
        let ticks = log(1 - fraction) / log(1 - SteerPolicy.integratorK)
        return ticks * Double(SteerPolicy.integratorTickMs)
    }
}
