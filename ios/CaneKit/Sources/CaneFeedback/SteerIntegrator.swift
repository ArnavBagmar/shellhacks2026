/// The two channels the steering cue renders, integrated together.
///
/// `lateral` is Shepherd's steering command after the navigation blend (their
/// stage 6 into stage 8) and drives the stereo pan; `proximity` is their
/// `proximityFactor` and drives the pulse rate. Both are integrated, not just
/// the steering: a 3 Hz `proximity` stepping straight into the pulse rate
/// would make the rate jump between frames, which reads as two different cues
/// rather than one closing in.
///
/// Neutral is (0, 0) -- dead centre and silent -- which is why the failsafe's
/// "decay to neutral" and "stop the cue" are the same instruction.
public struct SteerIntegrator: Equatable, Sendable {
    public var lateral = LeakyIntegrator()
    public var proximity = LeakyIntegrator()

    public init(lateral: LeakyIntegrator = LeakyIntegrator(), proximity: LeakyIntegrator = LeakyIntegrator()) {
        self.lateral = lateral
        self.proximity = proximity
    }

    public var isNeutral: Bool { lateral.isNeutral && proximity.isNeutral }

    public func advanced(lateral target: Double, proximity proximityTarget: Double, elapsedMs: Int64) -> SteerIntegrator {
        SteerIntegrator(
            lateral: lateral.advanced(toward: target, elapsedMs: elapsedMs),
            proximity: proximity.advanced(toward: proximityTarget, elapsedMs: elapsedMs)
        )
    }

    /// Both channels decay at the same rate, so the cue fades out centred and
    /// slowing rather than drifting to one side as it stops.
    public func decayed(elapsedMs: Int64) -> SteerIntegrator {
        SteerIntegrator(
            lateral: lateral.decayed(elapsedMs: elapsedMs),
            proximity: proximity.decayed(elapsedMs: elapsedMs)
        )
    }
}
