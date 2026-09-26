/// The single implementation of every bound and conditional check in the
/// contract. Both construction paths -- `AwarenessEvent.init(from decoder:)`
/// and the public memberwise `init(...) throws` -- call these functions, so
/// an event built in memory can never be laxer than a decoded one.
///
/// Type/shape and presence errors (`wrongType`, `missingKey`, `unknownKeys`)
/// belong to the decoder alone and are not represented here: a typed Swift
/// argument cannot be the wrong type or absent.
enum AwarenessEventFieldChecks {
    static func schemaVersion(_ value: Int) throws {
        guard value == AwarenessEventBounds.supportedSchemaVersion else {
            throw AwarenessEventError.outOfRange(field: "schema")
        }
    }

    static func trackId(_ value: String) throws {
        guard
            value.count >= AwarenessEventBounds.trackIdMinLength,
            value.count <= AwarenessEventBounds.trackIdMaxLength
        else {
            throw AwarenessEventError.outOfRange(field: "track_id")
        }
    }

    static func tCaptureMs(_ value: Int) throws {
        guard
            value >= AwarenessEventBounds.tCaptureMsMin,
            value <= AwarenessEventBounds.tCaptureMsMax
        else {
            throw AwarenessEventError.outOfRange(field: "t_capture_ms")
        }
    }

    static func ttlMs(_ value: Int) throws {
        guard
            value >= AwarenessEventBounds.ttlMsMin,
            value <= AwarenessEventBounds.ttlMsMax
        else {
            throw AwarenessEventError.outOfRange(field: "ttl_ms")
        }
    }

    static func confidence(_ value: Double) throws {
        guard
            value.isFinite,
            value >= AwarenessEventBounds.confidenceMin,
            value <= AwarenessEventBounds.confidenceMax
        else {
            throw AwarenessEventError.outOfRange(field: "confidence")
        }
    }

    static func bearingDeg(_ value: Double) throws {
        guard
            value.isFinite,
            value > AwarenessEventBounds.bearingDegMinExclusive,
            value <= AwarenessEventBounds.bearingDegMax
        else {
            throw AwarenessEventError.outOfRange(field: "bearing_deg")
        }
    }

    static func rangeM(_ value: Double?) throws {
        guard let value else { return }
        guard
            value.isFinite,
            value >= AwarenessEventBounds.rangeMMin,
            value <= AwarenessEventBounds.rangeMMax
        else {
            throw AwarenessEventError.outOfRange(field: "range_m")
        }
    }

    static func closingMps(_ value: Double?) throws {
        guard let value else { return }
        guard
            value.isFinite,
            value >= AwarenessEventBounds.closingMpsMin,
            value <= AwarenessEventBounds.closingMpsMax
        else {
            throw AwarenessEventError.outOfRange(field: "closing_mps")
        }
    }

    /// Cross-field rule: only the phone owns metric range (§4.5).
    static func metricRangeOwnership(source: Source, rangeM: Double?) throws {
        guard source == .phone || rangeM == nil else {
            throw AwarenessEventError.x4StatedMetres
        }
    }

    /// Every check, in the same order the decoder applies them.
    static func all(
        schemaVersion schema: Int,
        source: Source,
        trackId track: String,
        tCaptureMs capture: Int,
        ttlMs ttl: Int,
        confidence conf: Double,
        bearingDeg bearing: Double,
        rangeM range: Double?,
        closingMps closing: Double?
    ) throws {
        try schemaVersion(schema)
        try trackId(track)
        try tCaptureMs(capture)
        try ttlMs(ttl)
        try confidence(conf)
        try bearingDeg(bearing)
        try rangeM(range)
        try closingMps(closing)
        try metricRangeOwnership(source: source, rangeM: range)
    }
}
