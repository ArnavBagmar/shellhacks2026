import CaneContracts

/// Gate 2 (A1.2). `now - t_capture > ttl` -> drop and count. Silence is a
/// valid output; announcing a bearing that has stopped being true is not.
///
/// The same predicate runs again at dequeue (A11), because an event can go
/// stale while it waits behind an utterance.
public enum StalenessGate {
    /// Strict `>`: an event exactly at its TTL is still true.
    public static func isStale(_ event: AwarenessEvent, now: Int64) -> Bool {
        now - Int64(event.tCaptureMs) > TTLTable.clampedTtlMs(for: event)
    }

    /// Dequeue-time form (A11): the queued item already knows when it dies,
    /// so the re-gate does not need the event again.
    public static func isExpired(expiresAtMs: Int64, now: Int64) -> Bool {
        now > expiresAtMs
    }
}
