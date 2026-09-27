import CaneContracts

/// A14. Every way an event can end, counted. Exactly one of these feeds the
/// §3.4 transport fallback -- `staleDrops` -- and H4 is explicit that
/// `futureDated` and `queueExpired` must never be summed into it.
public struct FeedbackCounters: Equatable, Sendable {
    /// Everything handed to `ingest`, valid or not.
    public var eventsReceived = 0
    public var invalidRejected = 0
    /// Stamped ahead of the phone clock: a clock-offset problem, not a
    /// detector problem.
    public var futureDated = 0
    /// §4.4: the one signal the fallback reads.
    public var staleDrops = 0
    public var staleDropsByClass: [ObjectClass: Int] = [:]
    /// What the drop *would* have said, had it arrived in time.
    public var staleDropsByWouldBeUrgency: [Int: Int] = [:]
    /// A transport replay of an identical `(source, track_id, t_capture_ms)`.
    public var duplicateExact = 0
    public var deduped = 0
    public var dedupedSeam = 0
    public var refractorySuppressed = 0
    public var spatialSuppressed = 0
    public var hysteresisHeld = 0
    /// Gate 5b: Stops lowered to act-soon because the object's bearing was
    /// sweeping past rather than holding. A walk beside traffic where this
    /// stays at zero means the gate is not firing; a walk where it equals the
    /// urgency-3 count means it is capping everything.
    public var pathConflictCapped = 0
    /// Neither `range_m` nor `range_band`: scored as `far`.
    public var bandUnknown = 0
    public var ambientLogged = 0
    public var announced = 0
    public var preempted = 0
    public var queueOverflow = 0
    /// Went stale while queued; dropped at dequeue rather than said late.
    public var queueExpired = 0
    public var sinkTimeouts = 0

    // MARK: - Step 24: the steering path
    //
    // Kept apart from the awareness-event tallies above on purpose. H4 is
    // explicit that only `staleDrops` feeds the §3.4 transport fallback, and a
    // late `steer` frame is a steering problem, not a detector problem: summing
    // it in would flip the phone to local inference over a depth head that is
    // merely slow.

    /// Every `steer` frame handed to `ingest`, gated or not.
    public var steerReceived = 0
    /// Arrived already past its TTL (plan §3's `STEER_TTL_MS`).
    public var steerStaleDrops = 0
    /// Arrived older than the command already held: reordered on the wire.
    public var steerOutOfOrder = 0
    /// Was held, then aged out before the next frame came -- the analogue of
    /// `queueExpired`, not of `staleDrops`.
    public var steerExpired = 0
    /// Decays to neutral started, for any reason (staleness or link loss).
    public var steerFailsafes = 0
    /// Pulses actually handed to the renderer.
    public var steerPulses = 0

    public init() {}
}
