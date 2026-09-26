/// What the wearer was last *told* about one `(source, track_id)` (A9).
/// Written at presentation start, never at decision time: hysteresis
/// compares against what was actually heard, not against a level that was
/// computed and then suppressed.
public struct TrackRecord: Equatable, Sendable {
    public let lastUrgency: Int
    public let lastDriver: DrivingMetric
    /// Phone clock when presentation began -- the refractory anchor.
    public let presentedAtMs: Int64
    /// Refreshed by every accepted sighting, so an object still in view is
    /// not evicted mid-approach.
    public let lastSeenMs: Int64

    public init(lastUrgency: Int, lastDriver: DrivingMetric, presentedAtMs: Int64, lastSeenMs: Int64) {
        self.lastUrgency = lastUrgency
        self.lastDriver = lastDriver
        self.presentedAtMs = presentedAtMs
        self.lastSeenMs = lastSeenMs
    }

    func seen(at now: Int64) -> TrackRecord {
        TrackRecord(
            lastUrgency: lastUrgency,
            lastDriver: lastDriver,
            presentedAtMs: presentedAtMs,
            lastSeenMs: max(lastSeenMs, now)
        )
    }
}

/// The bounded map of track records (A15). Keyed by `(source, track_id)`;
/// 256 entries, evicting the least recently seen; entries age out after ten
/// seconds of not being seen.
public enum TrackRecords {
    public static let maxRecords = 256
    public static let ttlMs: Int64 = 10_000

    public static func pruned(
        _ tracks: [FeedbackKey: TrackRecord],
        now: Int64
    ) -> [FeedbackKey: TrackRecord] {
        tracks.filter { now - $0.value.lastSeenMs <= ttlMs }
    }

    /// A sighting: keeps an already-announced track alive without touching
    /// what was announced. A track that has never been announced has no
    /// record, and does not get one here.
    public static func touching(
        _ tracks: [FeedbackKey: TrackRecord],
        key: FeedbackKey,
        now: Int64
    ) -> [FeedbackKey: TrackRecord] {
        guard let existing = tracks[key] else { return tracks }
        var updated = tracks
        updated[key] = existing.seen(at: now)
        return updated
    }

    /// Presentation start: this is the only place `lastUrgency` is written.
    public static func recordingPresentation(
        _ tracks: [FeedbackKey: TrackRecord],
        key: FeedbackKey,
        urgency: Int,
        driver: DrivingMetric,
        now: Int64
    ) -> [FeedbackKey: TrackRecord] {
        var updated = pruned(tracks, now: now)
        updated[key] = TrackRecord(
            lastUrgency: urgency,
            lastDriver: driver,
            presentedAtMs: now,
            lastSeenMs: now
        )
        guard updated.count > maxRecords else { return updated }
        let surplus = updated.count - maxRecords
        let coldest = updated
            .sorted { $0.value.lastSeenMs < $1.value.lastSeenMs }
            .prefix(surplus)
            .map(\.key)
        for cold in coldest where cold != key {
            updated[cold] = nil
        }
        return updated
    }
}
