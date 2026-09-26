import CaneContracts
import Foundation

/// Builds `AwarenessEvent`s through the contract's validating initializer, so
/// no test can accidentally assert on an event the wire would have rejected.
/// Every parameter has a defensible default; each test overrides only the
/// fields it is actually about.
enum Make {
    /// A round phone-clock timestamp inside the contract's `t_capture_ms`
    /// bounds; every test builds its timeline relative to this.
    static let t0: Int64 = 1_790_000_000_000

    /// The contract maximum, so the class TTL table (never the producer) is
    /// what decides staleness unless a test says otherwise.
    static let permissiveTtlMs = 15_000

    static func event(
        source: Source = .phone,
        trackId: String = "t1",
        tCaptureMs: Int64 = t0,
        ttlMs: Int = permissiveTtlMs,
        cls: ObjectClass = .person,
        confidence: Double = 0.9,
        bearingDeg: Double = 0.0,
        rangeM: Double? = nil,
        rangeBand: RangeBand? = nil,
        closingMps: Double? = nil
    ) throws -> AwarenessEvent {
        try AwarenessEvent(
            source: source,
            trackId: trackId,
            tCaptureMs: tCaptureMs,
            ttlMs: ttlMs,
            cls: cls,
            confidence: confidence,
            bearingDeg: bearingDeg,
            rangeM: rangeM,
            rangeBand: rangeBand,
            closingMps: closingMps
        )
    }

    /// A phone event with trustworthy metric range (the collision zone of
    /// §4.5: |bearing| <= 37, range <= 15 m).
    static func phone(
        trackId: String = "phone-1",
        tCaptureMs: Int64 = t0,
        cls: ObjectClass = .person,
        confidence: Double = 0.9,
        bearingDeg: Double = 0.0,
        rangeM: Double,
        closingMps: Double? = nil,
        ttlMs: Int = permissiveTtlMs
    ) throws -> AwarenessEvent {
        try event(
            source: .phone,
            trackId: trackId,
            tCaptureMs: tCaptureMs,
            ttlMs: ttlMs,
            cls: cls,
            confidence: confidence,
            bearingDeg: bearingDeg,
            rangeM: rangeM,
            rangeBand: band(forMetres: rangeM),
            closingMps: closingMps
        )
    }

    /// An X4 event: bands only, `range_m` is null by contract.
    static func x4(
        source: Source = .x4Local,
        trackId: String = "x4-1",
        tCaptureMs: Int64 = t0,
        cls: ObjectClass = .cyclist,
        confidence: Double = 0.8,
        bearingDeg: Double = 90.0,
        rangeBand: RangeBand? = .mid,
        closingMps: Double? = nil,
        ttlMs: Int = permissiveTtlMs
    ) throws -> AwarenessEvent {
        try event(
            source: source,
            trackId: trackId,
            tCaptureMs: tCaptureMs,
            ttlMs: ttlMs,
            cls: cls,
            confidence: confidence,
            bearingDeg: bearingDeg,
            rangeM: nil,
            rangeBand: rangeBand,
            closingMps: closingMps
        )
    }

    /// `near` <= 5 m, `mid` 5-15 m, `far` > 15 m (contracts/README.md).
    static func band(forMetres metres: Double) -> RangeBand {
        if metres <= 5.0 { return .near }
        if metres <= 15.0 { return .mid }
        return .far
    }
}
