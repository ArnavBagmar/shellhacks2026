/// Producer of an `AwarenessEvent`. Mirrors the `source` enum in
/// `contracts/awareness_event.schema.json`.
public enum Source: String, Codable, CaseIterable, Sendable {
    case phone
    case x4Cloud = "x4_cloud"
    case x4Local = "x4_local"
}
