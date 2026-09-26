import Foundation

/// Eleven required fields, all three lanes code against this. Immutable:
/// every stored property is a `let`.
public struct AwarenessEvent: Codable, Equatable, Sendable {
    public let schemaVersion: Int
    public let source: Source
    public let trackId: String
    public let tCaptureMs: Int
    public let ttlMs: Int
    public let cls: ObjectClass
    public let confidence: Double
    public let bearingDeg: Double
    public let rangeM: Double?
    public let rangeBand: RangeBand?
    public let closingMps: Double?

    private enum CodingKeys: String, CodingKey, CaseIterable {
        case schemaVersion = "schema"
        case source
        case trackId = "track_id"
        case tCaptureMs = "t_capture_ms"
        case ttlMs = "ttl_ms"
        case cls
        case confidence
        case bearingDeg = "bearing_deg"
        case rangeM = "range_m"
        case rangeBand = "range_band"
        case closingMps = "closing_mps"
    }

    /// A dynamic `CodingKey` used only to enumerate every key actually
    /// present in the payload, so unknown keys can be rejected
    /// (`additionalProperties: false` has no declarative Codable
    /// equivalent).
    private struct AnyKey: CodingKey {
        let stringValue: String
        init?(stringValue: String) { self.stringValue = stringValue }
        var intValue: Int? { nil }
        init?(intValue: Int) { nil }
    }

    public init(from decoder: Decoder) throws {
        let anyContainer = try decoder.container(keyedBy: AnyKey.self)
        let allowedKeys = Set(CodingKeys.allCases.map(\.stringValue))
        let presentKeys = Set(anyContainer.allKeys.map(\.stringValue))
        let unknownKeys = presentKeys.subtracting(allowedKeys).sorted()
        guard unknownKeys.isEmpty else {
            throw AwarenessEventError.unknownKeys(unknownKeys)
        }

        let container = try decoder.container(keyedBy: CodingKeys.self)

        func require(_ key: CodingKeys) throws {
            guard container.contains(key) else {
                throw AwarenessEventError.missingKey(key.stringValue)
            }
        }

        func decodeField<T: Decodable>(_ type: T.Type, forKey key: CodingKeys) throws -> T {
            do {
                return try container.decode(type, forKey: key)
            } catch is DecodingError {
                throw AwarenessEventError.wrongType(field: key.stringValue)
            }
        }

        try require(.schemaVersion)
        let schemaVersion = try decodeField(Int.self, forKey: .schemaVersion)
        guard schemaVersion == 1 else {
            throw AwarenessEventError.outOfRange(field: "schema")
        }
        self.schemaVersion = schemaVersion

        try require(.source)
        self.source = try decodeField(Source.self, forKey: .source)

        try require(.trackId)
        let trackId = try decodeField(String.self, forKey: .trackId)
        guard
            trackId.count >= AwarenessEventBounds.trackIdMinLength,
            trackId.count <= AwarenessEventBounds.trackIdMaxLength
        else {
            throw AwarenessEventError.outOfRange(field: "track_id")
        }
        self.trackId = trackId

        try require(.tCaptureMs)
        let tCaptureMs = try decodeField(Int.self, forKey: .tCaptureMs)
        guard
            tCaptureMs >= AwarenessEventBounds.tCaptureMsMin,
            tCaptureMs <= AwarenessEventBounds.tCaptureMsMax
        else {
            throw AwarenessEventError.outOfRange(field: "t_capture_ms")
        }
        self.tCaptureMs = tCaptureMs

        try require(.ttlMs)
        let ttlMs = try decodeField(Int.self, forKey: .ttlMs)
        guard
            ttlMs >= AwarenessEventBounds.ttlMsMin,
            ttlMs <= AwarenessEventBounds.ttlMsMax
        else {
            throw AwarenessEventError.outOfRange(field: "ttl_ms")
        }
        self.ttlMs = ttlMs

        try require(.cls)
        self.cls = try decodeField(ObjectClass.self, forKey: .cls)

        try require(.confidence)
        let confidence = try decodeField(Double.self, forKey: .confidence)
        guard
            confidence.isFinite,
            confidence >= AwarenessEventBounds.confidenceMin,
            confidence <= AwarenessEventBounds.confidenceMax
        else {
            throw AwarenessEventError.outOfRange(field: "confidence")
        }
        self.confidence = confidence

        try require(.bearingDeg)
        let bearingDeg = try decodeField(Double.self, forKey: .bearingDeg)
        guard
            bearingDeg.isFinite,
            bearingDeg > AwarenessEventBounds.bearingDegMinExclusive,
            bearingDeg <= AwarenessEventBounds.bearingDegMax
        else {
            throw AwarenessEventError.outOfRange(field: "bearing_deg")
        }
        self.bearingDeg = bearingDeg

        // Nullable fields: decode as an Optional so a *missing* key throws
        // (via `require` above) but an explicit `null` decodes to nil.
        try require(.rangeM)
        let rangeM = try decodeField(Double?.self, forKey: .rangeM)
        if let rangeM {
            guard
                rangeM.isFinite,
                rangeM >= AwarenessEventBounds.rangeMMin,
                rangeM <= AwarenessEventBounds.rangeMMax
            else {
                throw AwarenessEventError.outOfRange(field: "range_m")
            }
        }
        self.rangeM = rangeM

        try require(.rangeBand)
        self.rangeBand = try decodeField(RangeBand?.self, forKey: .rangeBand)

        try require(.closingMps)
        let closingMps = try decodeField(Double?.self, forKey: .closingMps)
        if let closingMps {
            guard
                closingMps.isFinite,
                closingMps >= AwarenessEventBounds.closingMpsMin,
                closingMps <= AwarenessEventBounds.closingMpsMax
            else {
                throw AwarenessEventError.outOfRange(field: "closing_mps")
            }
        }
        self.closingMps = closingMps

        guard self.source == .phone || self.rangeM == nil else {
            throw AwarenessEventError.x4StatedMetres
        }
    }

    /// Hand-written, not synthesized: the synthesized `Encodable` would use
    /// `encodeIfPresent` and omit nil keys, so it would emit 8 keys and
    /// `AwarenessEvent.decode` would reject its own output.
    public func encode(to encoder: Encoder) throws {
        var container = encoder.container(keyedBy: CodingKeys.self)
        try container.encode(schemaVersion, forKey: .schemaVersion)
        try container.encode(source, forKey: .source)
        try container.encode(trackId, forKey: .trackId)
        try container.encode(tCaptureMs, forKey: .tCaptureMs)
        try container.encode(ttlMs, forKey: .ttlMs)
        try container.encode(cls, forKey: .cls)
        try container.encode(confidence, forKey: .confidence)
        try container.encode(bearingDeg, forKey: .bearingDeg)
        try container.encode(rangeM, forKey: .rangeM)
        try container.encode(rangeBand, forKey: .rangeBand)
        try container.encode(closingMps, forKey: .closingMps)
    }

    /// Rejected, never crashed: the only error this throws is
    /// `AwarenessEventError`.
    public static func decode(_ data: Data) throws -> AwarenessEvent {
        do {
            return try JSONDecoder().decode(AwarenessEvent.self, from: data)
        } catch let error as AwarenessEventError {
            throw error
        } catch {
            throw AwarenessEventError.malformedJSON
        }
    }

    /// Round-trips: `decode(try event.encoded()) == event`.
    public func encoded() throws -> Data {
        try JSONEncoder().encode(self)
    }
}
