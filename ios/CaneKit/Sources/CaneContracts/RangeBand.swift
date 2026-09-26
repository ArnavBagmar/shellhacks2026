/// `near` <= 5 m, `mid` 5-15 m, `far` > 15 m (see `contracts/README.md`).
/// Mirrors the `range_band` enum (minus `null`) in
/// `contracts/awareness_event.schema.json`.
public enum RangeBand: String, Codable, CaseIterable, Sendable {
    case near
    case mid
    case far
}
