import CaneTransport

/// When a `ground` cue is worth saying out loud.
///
/// A hole is not a car: it does not approach, it does not accelerate, and the
/// laptop re-sends the same one every tick for as long as it is in frame. Left
/// ungated that is three sentences a second about one pothole, which is the
/// flooding the refractory gate exists to prevent everywhere else.
///
/// This is deliberately *not* the hazard gauntlet. A `ground` cue has no
/// `track_id` to key a refractory on, no class, and no urgency ladder that
/// could score it -- the ladder needs metric range or a closing rate and a hole
/// has neither, which is why step 29 gave it its own frame in the first place.
/// So the throttle keys on the only two things the cue does carry: how long ago
/// the wearer was told, and whether it has moved.
public enum GroundGate {
    /// One reminder per six seconds while the same hole stays in view. At
    /// 1.4 m/s that is ~8 m of walking, so a hole that is still being reported
    /// after it is genuinely a second one, or the wearer has stopped.
    public static let refractoryMs: Int64 = 6_000

    /// A hazard this far from the last one announced is a *different* hazard
    /// and re-arms immediately. Wider than the 10 degrees the dedup gate uses
    /// for objects, because a ground cue's bearing is a column centroid and
    /// wanders as the wearer's gait rolls the camera.
    public static let rearmBearingDeg = 25.0

    public struct State: Equatable, Sendable {
        public let announcedAtMs: Int64?
        public let announcedBearingDeg: Double?

        public init(announcedAtMs: Int64? = nil, announcedBearingDeg: Double? = nil) {
            self.announcedAtMs = announcedAtMs
            self.announcedBearingDeg = announcedBearingDeg
        }
    }

    public enum Decision: Equatable, Sendable {
        case announce
        /// Older than its TTL: the wearer has walked past whatever this was.
        case stale
        /// The same hole, said recently enough.
        case throttled
    }

    /// Pure. Returns the decision and the state that follows it, so nothing
    /// here owns mutable state and the caller can test a whole walk as a fold.
    public static func decide(
        _ cue: GroundCue, state: State, now: Int64
    ) -> (decision: Decision, state: State) {
        guard now - cue.tCaptureMs <= Int64(cue.ttlMs) else {
            return (.stale, state)
        }
        if let lastAt = state.announcedAtMs, let lastBearing = state.announcedBearingDeg {
            let moved = AngularMath.absDelta(cue.bearingDeg, lastBearing) > rearmBearingDeg
            let waited = now - lastAt >= refractoryMs
            guard moved || waited else { return (.throttled, state) }
        }
        return (
            .announce,
            State(announcedAtMs: now, announcedBearingDeg: cue.bearingDeg)
        )
    }
}
