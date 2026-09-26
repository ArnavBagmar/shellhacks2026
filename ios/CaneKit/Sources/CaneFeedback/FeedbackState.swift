import CaneContracts

/// Everything the gauntlet remembers. A value type with no closures and no
/// references, so every stage can take one and return a new one, and a test
/// can assert on the whole thing.
public struct FeedbackState: Equatable, Sendable {
    /// What is being said right now. The `Task` doing the saying lives in
    /// the actor; only the facts the pump needs live here.
    public struct InFlight: Equatable, Sendable {
        public let id: UInt64
        public let urgency: Int
        public let key: FeedbackKey
    }

    public var counters = FeedbackCounters()
    public var rate = RateRing()
    /// Gate 3 and 4's memory (bounded by age and count).
    public var recentAccepted: [AcceptedRecord] = []
    /// What each track was last told and last seen (bounded, ages out).
    public var tracks: [FeedbackKey: TrackRecord] = [:]
    /// What was said, where (bounded at 16).
    public var recentAnnouncements: [AnnouncementRecord] = []
    /// Pending announcements (bounded at 8).
    public var queue: [QueuedItem] = []
    public var inFlight: InFlight?
    /// When speech last started -- the throttle's anchor. Haptic-only
    /// announcements do not move it.
    public var lastSpeechAtMs: Int64?
    public var nextAnnouncementId: UInt64 = 1
    /// Step 24 (Shepherd's stages 6, 8, 9). Shares the actor and the 100 ms
    /// tick with the announcement gauntlet and touches none of its state.
    public var steering = SteeringState()

    public init() {}

    /// §3.4's trigger: stale drops over events received in the trailing
    /// 10 s. 0 when nothing was received.
    public func staleDropRate(now: Int64) -> Double {
        rate.rate(now: now)
    }

    /// The boolean step 8 switches on (H4).
    public func isStaleDropRateUnhealthy(now: Int64) -> Bool {
        rate.isUnhealthy(now: now)
    }
}
