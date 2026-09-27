import Foundation

/// Which engine should be getting the wearer somewhere.
///
/// Indoors there is no GPS and no floorplan, so the laptop's sign memory is
/// the only thing that knows where anything is. Outdoors the Routes engine in
/// this module knows the whole city. The wearer says the same sentence either
/// way; this is what decides who answers it.
public enum WalkSetting: String, Sendable, Equatable {
    case indoors
    case outdoors
}

/// Indoors or outdoors, decided from how well GPS is working.
///
/// **Why accuracy and not a sensor.** There is no indoor/outdoor sensor. What
/// there is, is a horizontal accuracy figure that collapses the moment the sky
/// is gone: outside, a phone reports single-digit metres; inside, the fix is
/// inferred from wifi and cell and the reported accuracy balloons, or stops
/// arriving at all. That is a weak signal on its own, which is why nothing
/// here flips on one reading.
///
/// **Three guards against flapping**, because a mode that oscillates in a
/// glass lobby would hand the wearer back and forth between two different
/// voices mid-sentence:
///
/// * a **dead band** -- between the two thresholds there is no vote at all,
///   so a fix drifting across one number cannot toggle anything;
/// * a **dwell** -- a changed verdict must hold for `dwellMs` before the mode
///   actually moves;
/// * **no fix is evidence too** -- GPS silence past `staleFixMs` votes
///   indoors rather than leaving the last outdoor verdict standing forever.
///
/// The first confident reading is adopted immediately: at start-up there is no
/// previous mode to protect, and making the wearer wait out a dwell before the
/// device knows where it is would be worse than useless.
///
/// A value type on purpose. Every reading returns a **new** estimator rather
/// than mutating this one, so the caller can compare `setting` before and
/// after to know whether to announce a change, and nothing can flip the mode
/// as a side effect.
public struct IndoorOutdoorEstimator: Sendable, Equatable {
    public struct Policy: Sendable, Equatable {
        /// At or below this, the phone can see the sky.
        public var outdoorAccuracyM: Double
        /// At or above this, the fix is inferred rather than measured.
        public var indoorAccuracyM: Double
        /// How long a changed verdict must hold before the mode moves.
        public var dwellMs: Int64
        /// Silence from GPS for this long is itself a vote for indoors.
        public var staleFixMs: Int64

        public init(
            outdoorAccuracyM: Double = 20,
            indoorAccuracyM: Double = 35,
            dwellMs: Int64 = 8_000,
            staleFixMs: Int64 = 15_000
        ) {
            self.outdoorAccuracyM = outdoorAccuracyM
            self.indoorAccuracyM = indoorAccuracyM
            self.dwellMs = dwellMs
            self.staleFixMs = staleFixMs
        }
    }

    private struct Vote: Sendable, Equatable {
        let setting: WalkSetting
        let sinceMs: Int64
    }

    public let policy: Policy
    /// `nil` until the first confident reading. Not a default of "outdoors":
    /// guessing would send an indoor question to a Maps engine with no fix.
    public let setting: WalkSetting?
    private let pending: Vote?
    private let lastFixMs: Int64?

    public init(policy: Policy = Policy(), setting: WalkSetting? = nil) {
        self.policy = policy
        self.setting = setting
        self.pending = nil
        self.lastFixMs = nil
    }

    private init(policy: Policy, setting: WalkSetting?, pending: Vote?, lastFixMs: Int64?) {
        self.policy = policy
        self.setting = setting
        self.pending = pending
        self.lastFixMs = lastFixMs
    }

    /// The estimator after one location fix.
    public func seeing(_ fix: LocationFix) -> IndoorOutdoorEstimator {
        applying(vote(for: fix.horizontalAccuracyM), atMs: fix.timestampMs, fixArrivedMs: fix.timestampMs)
    }

    /// The estimator after a moment with no fix at all.
    ///
    /// Call this on a timer as well as on fixes: a phone carried inside stops
    /// producing them, and "the last thing I heard was outdoors" must not
    /// outlive the walk into the building.
    public func seeingNothing(atMs nowMs: Int64) -> IndoorOutdoorEstimator {
        guard let lastFixMs else {
            // Never had a fix at all. Location may be denied, or we started
            // indoors -- either way, indoors is the safe reading, because it
            // routes to the engine that does not need a fix.
            return applying(.indoors, atMs: nowMs, fixArrivedMs: nil)
        }
        guard nowMs - lastFixMs >= policy.staleFixMs else { return self }
        return applying(.indoors, atMs: nowMs, fixArrivedMs: nil)
    }

    /// Which way one accuracy figure votes, or `nil` inside the dead band.
    private func vote(for accuracyM: Double) -> WalkSetting? {
        if accuracyM <= policy.outdoorAccuracyM { return .outdoors }
        if accuracyM >= policy.indoorAccuracyM { return .indoors }
        return nil
    }

    private func applying(
        _ observed: WalkSetting?, atMs nowMs: Int64, fixArrivedMs: Int64?
    ) -> IndoorOutdoorEstimator {
        let seenAt = fixArrivedMs ?? lastFixMs
        // No vote, or a vote for where we already are: clear any half-formed
        // change so a flicker cannot accumulate into a flip.
        guard let observed, observed != setting else {
            return IndoorOutdoorEstimator(policy: policy, setting: setting, pending: nil, lastFixMs: seenAt)
        }
        // Nothing known yet: adopt at once, there is no mode to protect.
        guard setting != nil else {
            return IndoorOutdoorEstimator(policy: policy, setting: observed, pending: nil, lastFixMs: seenAt)
        }
        guard let standing = pending, standing.setting == observed else {
            return IndoorOutdoorEstimator(
                policy: policy, setting: setting,
                pending: Vote(setting: observed, sinceMs: nowMs), lastFixMs: seenAt
            )
        }
        guard nowMs - standing.sinceMs >= policy.dwellMs else {
            return IndoorOutdoorEstimator(policy: policy, setting: setting, pending: standing, lastFixMs: seenAt)
        }
        return IndoorOutdoorEstimator(policy: policy, setting: observed, pending: nil, lastFixMs: seenAt)
    }
}
