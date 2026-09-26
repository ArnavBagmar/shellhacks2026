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
        try AwarenessEventFieldChecks.schemaVersion(schemaVersion)
        self.schemaVersion = schemaVersion

        try require(.source)
        self.source = try decodeField(Source.self, forKey: .source)

        try require(.trackId)
        let trackId = try decodeField(String.self, forKey: .trackId)
        try AwarenessEventFieldChecks.trackId(trackId)
        self.trackId = trackId

        try require(.tCaptureMs)
        let tCaptureMs = try decodeField(Int.self, forKey: .tCaptureMs)
        try AwarenessEventFieldChecks.tCaptureMs(tCaptureMs)
        self.tCaptureMs = tCaptureMs

        try require(.ttlMs)
        let ttlMs = try decodeField(Int.self, forKey: .ttlMs)
        try AwarenessEventFieldChecks.ttlMs(ttlMs)
        self.ttlMs = ttlMs

        try require(.cls)
        self.cls = try decodeField(ObjectClass.self, forKey: .cls)

        try require(.confidence)
        let confidence = try decodeField(Double.self, forKey: .confidence)
        try AwarenessEventFieldChecks.confidence(confidence)
        self.confidence = confidence

        try require(.bearingDeg)
        let bearingDeg = try decodeField(Double.self, forKey: .bearingDeg)
        try AwarenessEventFieldChecks.bearingDeg(bearingDeg)
        self.bearingDeg = bearingDeg

        // Nullable fields: decode as an Optional so a *missing* key throws
        // (via `require` above) but an explicit `null` decodes to nil.
        try require(.rangeM)
        let rangeM = try decodeField(Double?.self, forKey: .rangeM)
        try AwarenessEventFieldChecks.rangeM(rangeM)
        self.rangeM = rangeM

        try require(.rangeBand)
        self.rangeBand = try decodeField(RangeBand?.self, forKey: .rangeBand)

        try require(.closingMps)
        let closingMps = try decodeField(Double?.self, forKey: .closingMps)
        try AwarenessEventFieldChecks.closingMps(closingMps)
        self.closingMps = closingMps

        try AwarenessEventFieldChecks.metricRangeOwnership(
            source: self.source,
            rangeM: self.rangeM
        )
    }

    /// Builds an event in memory, running exactly the bound and conditional
    /// checks `init(from decoder:)` runs (`AwarenessEventFieldChecks`), so a
    /// producer or a test can never hand the rest of the system an event the
    /// wire would have rejected.
    ///
    /// `tCaptureMs` is taken as `Int64` because the phone clock is milliseconds
    /// since the epoch; it is range-checked before narrowing to the stored
    /// `Int`, so the conversion cannot trap.
    public init(
        schema: Int = AwarenessEventBounds.supportedSchemaVersion,
        source: Source,
        trackId: String,
        tCaptureMs: Int64,
        ttlMs: Int,
        cls: ObjectClass,
        confidence: Double,
        bearingDeg: Double,
        rangeM: Double?,
        rangeBand: RangeBand?,
        closingMps: Double?
    ) throws {
        guard let captureMs = Int(exactly: tCaptureMs) else {
            throw AwarenessEventError.outOfRange(field: "t_capture_ms")
        }
        try AwarenessEventFieldChecks.all(
            schemaVersion: schema,
            source: source,
            trackId: trackId,
            tCaptureMs: captureMs,
            ttlMs: ttlMs,
            confidence: confidence,
            bearingDeg: bearingDeg,
            rangeM: rangeM,
            closingMps: closingMps
        )
        self.schemaVersion = schema
        self.source = source
        self.trackId = trackId
        self.tCaptureMs = captureMs
        self.ttlMs = ttlMs
        self.cls = cls
        self.confidence = confidence
        self.bearingDeg = bearingDeg
        self.rangeM = rangeM
        self.rangeBand = rangeBand
        self.closingMps = closingMps
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
