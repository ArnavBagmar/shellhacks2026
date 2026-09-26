import CaneTransport

/// Everything step 24 remembers, as a value -- the same rule the rest of
/// `FeedbackState` follows, so every stage can take one and return a new one
/// and a test can assert on the whole thing.
///
/// It hangs off `FeedbackState` rather than living in a second actor: the
/// steering cue is driven by the tick `FeedbackManager` already runs, and two
/// actors sharing one Taptic Engine is how you get two patterns superimposed.
public struct SteeringState: Equatable, Sendable {
    /// Shepherd's stage 8 state, the only thing that survives a frame.
    public var integrator = SteerIntegrator()
    /// The last accepted command. `nil` means *nothing is held* -- the phone
    /// has no steering evidence, which is different from a steer of zero
    /// (plan §4.2: no silent fakes).
    public var command: SteerCommand?
    /// `StalenessGate.expiresAtMs(command)`, cached so the tick's re-check is
    /// a comparison.
    public var commandExpiresAtMs: Int64?
    /// Whether the last frame's `proximity` was metre-anchored (plan §2.3).
    /// Kept on the state rather than read off `command`, so the failsafe decay
    /// keeps rendering at the same kind of rate after the command is dropped.
    /// Defaults to `false`: a proximity we have no anchor for is a band.
    public var anchored = false
    /// The current navigation turn cue's pull, if any (Shepherd's stage 6).
    public var navigationBias: NavigationBias?
    /// False from the moment the app knows the socket is gone. Belt and braces
    /// over the TTL: staleness alone would decay after 400 ms anyway, but a
    /// known-dead link should not have to wait that out.
    public var linkUp = true
    /// When the integrator was last advanced. `nil` until there is something
    /// to integrate.
    public var lastTickMs: Int64?
    public var pulse = SteerPulseClock()
    /// Whether the cue is currently sounding. Feeds the quiet gate's
    /// hysteresis and makes `silence()` fire exactly once.
    public var isAudible = false

    public init() {}

    /// True while the held command is still worth acting on.
    public func isLive(now: Int64) -> Bool {
        guard linkUp, command != nil, let expiry = commandExpiresAtMs else { return false }
        return !StalenessGate.isExpired(expiresAtMs: expiry, now: now)
    }
}
