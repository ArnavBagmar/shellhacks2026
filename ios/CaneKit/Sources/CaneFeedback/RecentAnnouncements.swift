import CaneContracts

/// What was actually said, where, and when (A6). Bearing-keyed rather than
/// track-keyed, because the whole point is to catch the same object arriving
/// under a *different* key: a cloud-to-local failover, a crop seam, an X4
/// track crossing into the phone's sector.
public struct AnnouncementRecord: Equatable, Sendable {
    public let key: FeedbackKey
    public let cls: ObjectClass
    public let bearingDeg: Double
    public let urgency: Int
    public let presentedAtMs: Int64

    public init(key: FeedbackKey, cls: ObjectClass, bearingDeg: Double, urgency: Int, presentedAtMs: Int64) {
        self.key = key
        self.cls = cls
        self.bearingDeg = bearingDeg
        self.urgency = urgency
        self.presentedAtMs = presentedAtMs
    }
}

/// The bounded list (A6, A15): sixteen records, pruned by age. The longest
/// window any rule of the spatial refractory looks back over is
/// `refractory(1)`, so nothing older can change a decision.
public enum RecentAnnouncements {
    public static let maxRecords = 16
    public static let retentionMs: Int64 = 8_000

    public static func pruned(_ records: [AnnouncementRecord], now: Int64) -> [AnnouncementRecord] {
        records.filter { now - $0.presentedAtMs <= retentionMs }
    }

    public static func appending(
        _ record: AnnouncementRecord,
        to records: [AnnouncementRecord],
        now: Int64
    ) -> [AnnouncementRecord] {
        let kept = pruned(records, now: now) + [record]
        guard kept.count > maxRecords else { return kept }
        return Array(kept.suffix(maxRecords))
    }
}
