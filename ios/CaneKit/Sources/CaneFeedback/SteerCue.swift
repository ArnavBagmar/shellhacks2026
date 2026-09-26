/// One pulse of the steering cue: what the wearer feels right now.
///
/// The A13 seam again -- step 24 decides *what* (which side, how urgent, how
/// often), `CaneOutput` decides *how* (which pattern, which gain, how panned).
/// Deliberately tiny and free of CoreHaptics, exactly like `Announcement`.
public struct SteerCue: Equatable, Sendable {
    /// The integrated, navigation-blended steering command, -1...+1, positive
    /// = right. Drives the stereo pan.
    public let lateral: Double
    /// The integrated `proximityFactor`, 0...1. Drives the pulse rate, and is
    /// what the quiet gate judged before this cue was emitted at all.
    public let proximity: Double
    /// The interval this pulse belongs to, so the renderer and the logs can
    /// say what rate the wearer is being given without recomputing it.
    public let intervalMs: Int64

    public init(lateral: Double, proximity: Double, intervalMs: Int64) {
        self.lateral = lateral
        self.proximity = proximity
        self.intervalMs = intervalMs
    }
}

/// Where the steering cue goes. Synchronous and non-blocking for the same
/// reason `AmbientSink` is: it is called from inside the tick, and a tick that
/// waits on a renderer is a tick that stops integrating.
public protocol SteerCueSink: Sendable {
    /// Play one pulse now. Must return immediately.
    func pulse(_ cue: SteerCue)
    /// Stop the cue. Called **once** when it goes quiet -- on the quiet
    /// threshold, on staleness, or on link loss -- never once per tick. Must
    /// be idempotent and safe when nothing is playing.
    func silence()
}

/// The default: no steering cue. Keeps every caller that predates step 24
/// (and every test that is not about steering) working unchanged.
public struct NoSteerCueSink: SteerCueSink, Sendable {
    public init() {}
    public func pulse(_ cue: SteerCue) {}
    public func silence() {}
}
