import CaneTransport

/// Step 24's stage, in the same shape as every other stage in this module: a
/// pure function of state that returns the next state and an intent (A16).
/// The actor owns the state, the clock and the sink; nothing here touches any
/// of the three.
///
/// Shepherd's stages 6, 8 and 9 all live in `advance`: blend the navigation
/// bias, integrate, and -- when the evidence has stopped being true -- decay
/// to neutral and stop.
public enum Steering {

    /// What the tick decided. `quiet` is the common case: the integrator moved
    /// but this tick is not a pulse.
    public enum Effect: Equatable, Sendable {
        case quiet
        case pulse(SteerCue)
        /// The cue stopped. Emitted once per stop, not once per tick.
        case silence
    }

    // MARK: - Inputs

    /// One `steer` frame from the laptop, gated on its own TTL.
    ///
    /// Nothing is rendered here. The integrator only ever moves on the tick,
    /// so a burst of frames (a reconnect flushing a backlog) cannot jump the
    /// cue -- which is the whole reason plan §1.3 asks for an integrator.
    public static func accept(_ command: SteerCommand, into state: FeedbackState, now: Int64) -> FeedbackState {
        var working = state
        working.counters.steerReceived += 1

        if StalenessGate.isStale(command, now: now) {
            // Dropped and counted, exactly as a stale event is -- but into its
            // own counter: H4 is explicit that only `staleDrops` feeds the
            // step-8 transport fallback, and a late steering frame is not a
            // detector problem.
            working.counters.steerStaleDrops += 1
            return working
        }
        if let held = working.steering.command, command.tCaptureMs < held.tCaptureMs {
            // Reordered on the wire. An older command must not undo a newer
            // one's steering.
            working.counters.steerOutOfOrder += 1
            return working
        }

        working.steering.command = command
        working.steering.commandExpiresAtMs = StalenessGate.expiresAtMs(command)
        working.steering.anchored = command.proximityAnchored
        // A frame arriving is stronger evidence that the link is alive than
        // any poll of the socket, and it arrives sooner.
        working.steering.linkUp = true
        // Integration starts when there is something to integrate, so the
        // first tick after the first frame measures from the frame.
        if working.steering.lastTickMs == nil { working.steering.lastTickMs = now }
        return working
    }

    /// Shepherd's stage 9's trigger. `false` on a dropped socket: the phone
    /// must not ride out the TTL on a link it knows is gone.
    public static func setLink(up: Bool, in state: FeedbackState) -> FeedbackState {
        var working = state
        working.steering.linkUp = up
        return working
    }

    /// Shepherd's stage 6's input: the current navigation turn cue's lateral
    /// pull, which expires on its own (`SteerPolicy.navigationBiasTtlMs`).
    public static func bias(lateral: Double, in state: FeedbackState, now: Int64) -> FeedbackState {
        var working = state
        working.steering.navigationBias = NavigationBias(lateral: lateral, now: now)
        return working
    }

    // MARK: - The tick

    public static func advance(_ state: FeedbackState, now: Int64) -> (FeedbackState, Effect) {
        var working = state
        let elapsedMs = max(0, now - (working.steering.lastTickMs ?? now))
        working.steering.lastTickMs = now
        if let bias = working.steering.navigationBias,
           StalenessGate.isExpired(expiresAtMs: bias.expiresAtMs, now: now) {
            working.steering.navigationBias = nil
        }

        if working.steering.isLive(now: now), let command = working.steering.command {
            working.steering.integrator = working.steering.integrator.advanced(
                lateral: SteerBlend.lateralTarget(
                    steer: command.steer,
                    navigationBias: working.steering.navigationBias?.lateral(now: now) ?? 0,
                    proximityFactor: command.proximity
                ),
                proximity: command.proximity,
                elapsedMs: elapsedMs
            )
        } else {
            working = startingFailsafe(working)
            // The decay measures from this tick, which may be up to one tick
            // *after* the command expired -- so the cue reaches neutral
            // slightly sooner than `FAILSAFE_DECAY_MS` from the expiry
            // instant, never later. Failing early is the safe direction.
            working.steering.integrator = working.steering.integrator.decayed(elapsedMs: elapsedMs)
        }

        return renderDecision(working, elapsedMs: elapsedMs)
    }

    /// Drops the held command the first tick after its evidence stopped being
    /// true. "Never hold a stale steer": the state is cleared here, so nothing
    /// downstream can read a command the gate has already rejected.
    private static func startingFailsafe(_ state: FeedbackState) -> FeedbackState {
        var working = state
        guard working.steering.command != nil else { return working }
        if working.steering.linkUp { working.counters.steerExpired += 1 }
        working.counters.steerFailsafes += 1
        working.steering.command = nil
        working.steering.commandExpiresAtMs = nil
        return working
    }

    /// The quiet gate (plan risk 3) and the pulse clock. Below the threshold
    /// the cue does not exist; above it, it pulses at the rate `proximity`
    /// asks for and no faster.
    private static func renderDecision(_ state: FeedbackState, elapsedMs: Int64) -> (FeedbackState, Effect) {
        var working = state
        let proximity = working.steering.integrator.proximity.value
        guard SteerPolicy.isAudible(proximity: proximity, wasAudible: working.steering.isAudible) else {
            guard working.steering.isAudible else { return (working, .quiet) }
            working.steering.isAudible = false
            working.steering.pulse = SteerPulseClock()
            return (working, .silence)
        }

        let intervalMs = SteerPolicy.pulseIntervalMs(proximity: proximity, anchored: working.steering.anchored)
        let (pulse, fired) = working.steering.pulse.advanced(elapsedMs: elapsedMs, intervalMs: intervalMs)
        working.steering.pulse = pulse
        working.steering.isAudible = true
        guard fired else { return (working, .quiet) }

        working.counters.steerPulses += 1
        return (working, .pulse(SteerCue(
            lateral: working.steering.integrator.lateral.value,
            proximity: proximity,
            intervalMs: intervalMs
        )))
    }
}
