import CaneContracts

/// One accepted event, remembered just long enough for the next producer's
/// version of the same object to show up (A3). Value type, no back
/// references: the queue and the state own it by copy.
public struct AcceptedRecord: Equatable, Sendable {
    public let key: FeedbackKey
    public let cls: ObjectClass
    public let bearingDeg: Double
    public let tCaptureMs: Int64
    /// Phone clock when the gauntlet saw it -- retention is by arrival,
    /// matching is by capture (A3).
    public let arrivalMs: Int64
    /// Cached so the dedup order can rank a record without the event.
    public let hasTrustedRange: Bool

    public init(event: AwarenessEvent, arrivalMs: Int64) {
        self.key = FeedbackKey(event)
        self.cls = event.cls
        self.bearingDeg = event.bearingDeg
        self.tCaptureMs = Int64(event.tCaptureMs)
        self.arrivalMs = arrivalMs
        self.hasTrustedRange = RangeTrust.isTrusted(event)
    }
}

/// Stage 3 of the gauntlet (A1.3) plus the memory stage 4 reads. Bounded two
/// ways (A15): by age, and by a hard record cap that a burst cannot exceed.
public enum RecentAccepted {
    /// The X4's version of an object arrives 200-500 ms after the phone's, so
    /// the memory has to outlive the 300 ms capture window by a wide margin.
    public static let retentionMs: Int64 = 1_200
    /// Hard cap: at 30 Hz from three sources the age window alone would hold
    /// ~100 records; this bounds the worst case regardless of event rate.
    public static let maxRecords = 64

    public static func pruned(_ records: [AcceptedRecord], now: Int64) -> [AcceptedRecord] {
        records.filter { now - $0.arrivalMs <= retentionMs }
    }

    /// Prunes by age, appends, then enforces the hard cap by dropping the
    /// oldest arrivals.
    public static func appending(
        _ record: AcceptedRecord,
        to records: [AcceptedRecord],
        now: Int64
    ) -> [AcceptedRecord] {
        let kept = pruned(records, now: now) + [record]
        guard kept.count > maxRecords else { return kept }
        return Array(kept.sorted { $0.arrivalMs < $1.arrivalMs }.suffix(maxRecords))
    }

    /// Gate 3: a transport reconnect can replay the same event. The triple
    /// `(source, track_id, t_capture_ms)` is exact -- a re-detection one
    /// millisecond later is a new event, not a replay.
    public static func containsExactDuplicate(
        of event: AwarenessEvent,
        in records: [AcceptedRecord]
    ) -> Bool {
        let key = FeedbackKey(event)
        let capture = Int64(event.tCaptureMs)
        return records.contains { $0.key == key && $0.tCaptureMs == capture }
    }
}
