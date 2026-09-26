import CaneContracts

/// §4.4: `t_capture_ms` exists for one purpose -- deciding whether a warning
/// is still true. Objects have different half-lives, so one global timeout
/// is wrong.
///
/// The switch is exhaustive over the 12-value class enum with **no
/// `default`** (A15): adding a class to the contract must break this build,
/// not silently inherit someone else's half-life.
public enum TTLTable {
    /// ~1.4 m/s.
    public static let personTtlMs: Int64 = 1_500
    /// 6.7 m/s -> 3.4 m of closure; past that the bearing is a lie.
    public static let cyclistTtlMs: Int64 = 500
    /// Faster closure than a cyclist.
    public static let carTtlMs: Int64 = 400
    /// It is not moving -- only you are.
    public static let staticTtlMs: Int64 = 8_000
    /// Controller ruling: an unclassified obstacle is treated as possibly
    /// moving, so it gets the pedestrian half-life, not the static one.
    public static let unknownTtlMs: Int64 = 1_500

    public static func ttlMs(for cls: ObjectClass) -> Int64 {
        switch cls {
        case .person: return personTtlMs
        case .cyclist: return cyclistTtlMs
        case .scooter: return cyclistTtlMs
        case .car: return carTtlMs
        case .bus: return carTtlMs
        case .pole: return staticTtlMs
        case .bollard: return staticTtlMs
        case .wall: return staticTtlMs
        case .parkedCar: return staticTtlMs
        case .door: return staticTtlMs
        case .overhang: return staticTtlMs
        case .unknownObstacle: return unknownTtlMs
        }
    }

    /// `min(producer ttl, class ttl)`: a producer may be more conservative
    /// than the table, never more optimistic.
    public static func clampedTtlMs(for event: AwarenessEvent) -> Int64 {
        min(Int64(event.ttlMs), ttlMs(for: event.cls))
    }

    /// The instant after which this event is a lie. Carried on the
    /// `Announcement` so the dequeue re-gate (A11) costs nothing.
    public static func expiresAtMs(_ event: AwarenessEvent) -> Int64 {
        Int64(event.tCaptureMs) + clampedTtlMs(for: event)
    }
}
