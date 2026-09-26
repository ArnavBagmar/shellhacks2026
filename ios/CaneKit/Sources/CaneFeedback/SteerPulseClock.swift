/// Turns "pulse every N ms" into "fire on this tick, or not".
///
/// A value type with no clock: the tick hands it elapsed time, the same
/// contract `LeakyIntegrator` has. The cue can only fire on a tick, so the
/// rate the wearer feels is quantised to `SteerPolicy.integratorTickMs` -- a
/// 250 ms interval is felt as 300 ms. That is accepted: the rate is a
/// gradient, not a measurement.
public struct SteerPulseClock: Equatable, Sendable {
    private var sinceLastPulseMs: Int64

    /// A fresh clock is **overdue**, so a cue that has just become audible
    /// pulses on the instant rather than one interval later: the wearer is
    /// already walking at whatever it is warning about.
    public init() {
        sinceLastPulseMs = SteerPolicy.pulseIntervalMaxMs
    }

    /// Accumulates `elapsedMs` and reports whether this tick is a pulse.
    ///
    /// On firing the accumulator resets to zero rather than subtracting the
    /// interval: after a long stall (the app was backgrounded) that spends the
    /// whole backlog on one pulse instead of machine-gunning the wearer with
    /// the pulses they missed.
    public func advanced(elapsedMs: Int64, intervalMs: Int64) -> (SteerPulseClock, fired: Bool) {
        var next = self
        next.sinceLastPulseMs += max(0, elapsedMs)
        guard next.sinceLastPulseMs >= intervalMs else { return (next, false) }
        next.sinceLastPulseMs = 0
        return (next, true)
    }
}
