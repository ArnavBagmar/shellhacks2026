import Foundation

/// Step 24's way onto the shared actuator.
///
/// Deliberately thin, and deliberately built out of `say` rather than beside
/// it: there is one Taptic Engine and one earcon node, and two producers
/// driving them independently superimpose into mush (`HapticRenderer.play`'s
/// own contract says so). So the steering cue goes through the same lock, the
/// same preemption and the same post-hazard speech gap everything else does --
/// it just sits at the bottom of the order and never waits.
extension OutputArbiter {
    /// Plays one steering pulse, or does not. Returns `false` when the cue was
    /// dropped because something above it owns the actuator (or because the
    /// wearer is still inside the 1800 ms gap after hazard speech, where the
    /// street is more useful to them than a third channel).
    ///
    /// Dropped, never queued, for the same reason a navigation cue is: a late
    /// pulse describes a corridor the wearer has already walked out of, and
    /// another one is due within 700 ms anyway.
    @discardableResult
    public func steerPulse(haptic: HapticScript?, pan: Double) async -> Bool {
        guard haptic != nil else { return false }
        return await say(nil, on: .steering, haptic: haptic, pan: pan, maxWaitMs: 0)
    }

    /// Stops a steering pulse in flight. Never touches a hazard, an answer, a
    /// prompt or a navigation cue -- `cancel(upTo:)` already refuses to.
    public func cancelSteering() {
        cancel(upTo: .steering)
    }
}
