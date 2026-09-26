import CaneContracts

/// A13 -- the boundary between step 9 (what to say) and step 10 (saying it).
/// Deliberately tiny: no CoreHaptics, no AVSpeechSynthesizer, no strings.
public protocol AnnouncementSink: Sendable {
    /// Returns when the announcement has finished or been cancelled. Must
    /// not throw: a renderer failure is the renderer's problem, and the
    /// gauntlet must not learn about it mid-gauntlet.
    ///
    /// H3: budget speech at <= 1.2 s and honour cancellation, or the pump's
    /// `PRESENT_TIMEOUT_MS` will cut it. An implementation **must** return
    /// immediately when its task is already cancelled on entry: preemption
    /// can cancel a presentation in the instant between the pump starting it
    /// and this method's first line.
    func present(_ announcement: Announcement) async

    /// Synchronous, non-blocking, idempotent, safe when idle. Called on
    /// preemption before the next announcement starts.
    func cancelCurrent()
}

/// The hazard-map hook for urgency 0 (§4.7's "log + hazard map, never
/// announced"). Synchronous and non-blocking: it runs inside the gauntlet.
public protocol AmbientSink: Sendable {
    func record(_ event: AwarenessEvent, urgency: Int)
}

/// The default: ambient events are counted and discarded until the hazard
/// map exists.
public struct NoAmbientSink: AmbientSink, Sendable {
    public init() {}
    public func record(_ event: AwarenessEvent, urgency: Int) {}
}
