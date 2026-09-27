import CaneContracts
import Foundation

/// How fast a track's bearing is sweeping, in degrees per second, computed
/// from the records the dedup gate already keeps.
///
/// **Why this exists.** `closing_mps` is radial: it answers "is the distance
/// between us shrinking", which a car passing three metres away answers with a
/// resounding yes. The question the wearer needs answered is "will this occupy
/// the space I am about to occupy", and those two differ by exactly the lateral
/// miss distance. A sweeping bearing means a miss; a bearing that holds while
/// the range closes is the classic collision course.
///
/// **Why there is no gyro in here.** There is no yaw source on this device --
/// no CoreMotion anywhere in the tree, and every heading lives in
/// `CaneNavigation`, deliberately walled off from the awareness path. Bearings
/// are torso-relative, so a turn sweeps every bearing at once. `commonMode`
/// is the answer: rotation is common to all tracks and a real passing car is
/// not, so the median of the other tracks *is* the rotation, and subtracting
/// it cancels the turn exactly. The same shape as the bridge's
/// `relative_freedom`, which cancels the affine depth parameters rather than
/// measuring them.
///
/// Nothing here guesses. Too few samples, or a frame with no duration, returns
/// `nil` — the reject-never-clamp rule `cloud/motion.py` already follows,
/// for the same reason: a fabricated rate is indistinguishable from a real one.
public enum BearingRate {
    private static let msPerS = 1_000.0

    /// Shorter than this, the pair is noise rather than a sweep.
    ///
    /// Derivation: bearing comes from a box centre in a 640 px crop over a
    /// 60 degree FOV, so a few pixels of detector jitter is ~1 degree. A
    /// 1 degree error across `dt` shows up as `1.41/dt` deg/s, which at 100 ms
    /// is ~14 deg/s -- comfortably under `PathConflict`'s 45 deg/s threshold,
    /// so jitter alone can never cap a Stop. At 20 ms the same jitter reads as
    /// 50 deg/s and would.
    ///
    /// The same guard `cloud/motion.py` puts on range rate (`MIN_DT_MS`), for
    /// the same reason.
    public static let minIntervalMs = 100

    /// Longer than this the two samples straddle a dropout, and their average
    /// says nothing about what the object is doing now. Matches the dedup
    /// memory's retention, which already bounds it.
    public static let maxIntervalMs = Int(RecentAccepted.retentionMs)

    /// Below this many *other* tracks there is no common-mode estimate. Two,
    /// not one: a single other track might itself be the moving car, and
    /// subtracting it would cancel the very signal the gate looks for.
    public static let minimumCommonModeTracks = 2

    /// Signed sweep for one track; positive is rightward, matching
    /// `bearing_deg`. `nil` when there is not enough to say.
    public static func forTrack(_ key: FeedbackKey, in records: [AcceptedRecord]) -> Double? {
        let mine = records.filter { $0.key == key }.sorted { $0.tCaptureMs < $1.tCaptureMs }
        guard let newest = mine.last,
              // Two detections sharing a capture stamp are one frame, and a
              // frame has no duration.
              let older = mine.last(where: { $0.tCaptureMs < newest.tCaptureMs })
        else { return nil }

        let dtMs = newest.tCaptureMs - older.tCaptureMs
        guard dtMs >= Int64(minIntervalMs), dtMs <= Int64(maxIntervalMs) else { return nil }

        // Never a plain subtraction: 175 and -175 are 10 apart, not -350, and
        // overtaking traffic lives exactly there.
        let sweepDeg = AngularMath.delta(newest.bearingDeg, older.bearingDeg)
        return sweepDeg * msPerS / Double(dtMs)
    }

    /// The median sweep of every track except `key` — this device's stand-in
    /// for a rate gyro. `nil` below `minimumCommonModeTracks`.
    public static func commonMode(
        excluding key: FeedbackKey, in records: [AcceptedRecord]
    ) -> Double? {
        let others = Set(records.map(\.key)).subtracting([key])
        // Sorted before the median, so an unordered Set cannot make this
        // non-deterministic.
        let rates = others.compactMap { forTrack($0, in: records) }.sorted()
        guard rates.count >= minimumCommonModeTracks else { return nil }
        return median(of: rates)
    }

    /// The track's own sweep with torso rotation removed when it can be
    /// estimated, and the raw sweep when it cannot — a lone car on an empty
    /// street is the common case and must still be judged.
    public static func corrected(_ key: FeedbackKey, in records: [AcceptedRecord]) -> Double? {
        guard let raw = forTrack(key, in: records) else { return nil }
        guard let rotation = commonMode(excluding: key, in: records) else { return raw }
        return raw - rotation
    }

    static func median(of sorted: [Double]) -> Double {
        let middle = sorted.count / 2
        guard sorted.count.isMultiple(of: 2) else { return sorted[middle] }
        return (sorted[middle - 1] + sorted[middle]) / 2
    }
}
