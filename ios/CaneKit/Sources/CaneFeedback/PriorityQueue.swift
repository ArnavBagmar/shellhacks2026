import CaneContracts

/// One pending announcement. Carries the level (not a bare `Int`), so a
/// queued ambient event is unrepresentable, and the expiry instant, so the
/// dequeue re-gate needs nothing else.
public struct QueuedItem: Equatable, Sendable {
    public let event: AwarenessEvent
    public let level: AnnouncementLevel
    /// The metric that produced the level, carried to the `TrackRecord`
    /// written at presentation start.
    public let driver: DrivingMetric
    public let expiresAtMs: Int64

    public init(event: AwarenessEvent, level: AnnouncementLevel, driver: DrivingMetric) {
        self.event = event
        self.level = level
        self.driver = driver
        self.expiresAtMs = TTLTable.expiresAtMs(event)
    }

    public var key: FeedbackKey { FeedbackKey(event) }
    public var urgency: Int { level.rawValue }
    public var tCaptureMs: Int64 { Int64(event.tCaptureMs) }
}

/// Stage 7 (A11). One announcement at a time, so the queue is short by
/// design: at most one pending item per track, at most eight in total, and
/// a Stop that cannot be pushed out by chatter.
public enum AnnouncementQueue {
    public static let maxDepth = 8

    /// Urgency descending, then capture time ascending: the most urgent
    /// thing first, and among equals the thing that happened first.
    public static func sorted(_ queue: [QueuedItem]) -> [QueuedItem] {
        queue.sorted { left, right in
            if left.urgency != right.urgency { return left.urgency > right.urgency }
            return left.tCaptureMs < right.tCaptureMs
        }
    }

    /// Insert-or-replace by key, then enforce the depth bound.
    /// `overflowed` is true when something (possibly the arriving item) was
    /// dropped to stay inside `maxDepth`.
    public static func inserting(
        _ item: QueuedItem,
        into queue: [QueuedItem]
    ) -> (queue: [QueuedItem], overflowed: Bool) {
        var working = queue
        if let existingIndex = working.firstIndex(where: { $0.key == item.key }) {
            let existing = working[existingIndex]
            // Replace only when more urgent, or equally urgent and newer. A newer
            // but LOWER-urgency read for the same key (a jittery TTC or band on the
            // next frame) must never displace a pending Stop.
            let better = item.urgency > existing.urgency
                || (item.urgency == existing.urgency && item.tCaptureMs > existing.tCaptureMs)
            guard better else { return (sorted(working), false) }
            working.remove(at: existingIndex)
        }
        working.append(item)
        guard working.count > maxDepth else { return (sorted(working), false) }

        // The victim is the least urgent, then the oldest -- including the
        // arriving item, which is how a Stop can only ever be evicted by
        // another Stop.
        let victim = working.min { left, right in
            if left.urgency != right.urgency { return left.urgency < right.urgency }
            return left.tCaptureMs < right.tCaptureMs
        }
        guard let victim else { return (sorted(working), false) }
        return (sorted(working.filter { $0.key != victim.key }), true)
    }

    /// Dedup losers that have not been presented yet (A4).
    public static func removing(keys: [FeedbackKey], from queue: [QueuedItem]) -> [QueuedItem] {
        guard !keys.isEmpty else { return queue }
        let doomed = Set(keys)
        return queue.filter { !doomed.contains($0.key) }
    }
}
