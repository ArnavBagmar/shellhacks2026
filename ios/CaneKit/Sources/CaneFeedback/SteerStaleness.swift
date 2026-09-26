import CaneTransport

/// Gate 2 for a `steer` frame (`docs/shepherd-steering-plan.md` step 24:
/// "gate it on `STEER_TTL_MS` through the existing staleness machinery").
///
/// Deliberately an extension on `StalenessGate` rather than a second
/// mechanism: the predicate that decides a steering command has stopped being
/// true is the same `now > expiresAtMs` the announcement queue re-gates on,
/// and there must be exactly one of those on this phone.
extension StalenessGate {
    /// `min(producer ttl, plan ttl)`. A producer may be more conservative than
    /// plan §3's 400 ms, never more optimistic -- the same clamp `TTLTable`
    /// applies to an `AwarenessEvent`'s class half-life.
    public static func clampedTtlMs(for command: SteerCommand) -> Int64 {
        min(Int64(command.ttlMs), Int64(SteerLimits.ttlMs))
    }

    /// The instant after which this command is a lie. Stored on the state so
    /// the tick's re-check costs nothing, exactly as `Announcement` carries
    /// its own `expiresAtMs`.
    public static func expiresAtMs(_ command: SteerCommand) -> Int64 {
        command.tCaptureMs + clampedTtlMs(for: command)
    }

    /// Strict `>`: a command exactly at its TTL is still true.
    public static func isStale(_ command: SteerCommand, now: Int64) -> Bool {
        isExpired(expiresAtMs: expiresAtMs(command), now: now)
    }
}
