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
    /// Neither `range_m` nor `range_band`: scored as `far`.
    public var bandUnknown = 0
    public var ambientLogged = 0
    public var announced = 0
    public var preempted = 0
    public var queueOverflow = 0
    /// Went stale while queued; dropped at dequeue rather than said late.
    public var queueExpired = 0
    public var sinkTimeouts = 0

    public init() {}
}
