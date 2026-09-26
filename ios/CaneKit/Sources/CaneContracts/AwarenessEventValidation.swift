/// Rejected, never crashed: `AwarenessEvent.decode` throws this and only
/// this. Mirrors the Python `AwarenessEventError` contract.
public enum AwarenessEventError: Error, Equatable, Sendable {
    /// The JSON was not an object matching the 11-key wire format at all
    /// (unparsable, or a type/shape mismatch at some key).
    case malformedJSON
    /// A key outside the 11 declared wire keys was present
    /// (`additionalProperties: false`).
    case unknownKeys([String])
    /// One of the 11 required keys was absent. An explicit `null` is not a
    /// missing key -- only an absent key is.
    case missingKey(String)
    /// A present key's value failed a numeric/length bound or enum
    /// membership check.
    case outOfRange(field: String)
    /// A key's JSON value was the wrong shape for its type
    /// (e.g. `"ttl_ms": "500"`).
    case wrongType(field: String)
    /// `range_m` was non-null on a non-phone `source`.
    case x4StatedMetres
}

/// Numeric bounds -- read from `contracts/awareness_event.schema.json`; see
/// `EnumDriftTests` for the equality assertions that keep them in sync.
public enum AwarenessEventBounds {
    /// The one supported wire version (`schema` is `const: 1`).
    public static let supportedSchemaVersion = 1
    public static let trackIdMinLength = 1
    public static let trackIdMaxLength = 64
    public static let tCaptureMsMin = 1_600_000_000_000
    public static let tCaptureMsMax = 4_000_000_000_000
    public static let ttlMsMin = 1
    public static let ttlMsMax = 15_000
    public static let confidenceMin = 0.0
    public static let confidenceMax = 1.0
    public static let bearingDegMinExclusive = -180.0
    public static let bearingDegMax = 180.0
    public static let rangeMMin = 0.0
    public static let rangeMMax = 50.0
    public static let closingMpsMin = -60.0
    public static let closingMpsMax = 60.0
}
