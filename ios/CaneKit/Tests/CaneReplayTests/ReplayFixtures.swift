import CaneContracts
import Foundation
import XCTest

/// Shared builders for the replay tests. The default event is the
/// car-from-the-right the PRD's geometry table is built on: `near`, closing
/// 6.2 m/s, confidence above the Stop floor.
enum Fixture {
    static let tCaptureMs: Int64 = 1_790_000_000_123
    /// 100 ms after capture: inside every TTL in the table, so nothing in
    /// these tests is ever dropped by the staleness gate by accident.
    static let emitMs: Int64 = tCaptureMs + 100

    static func event(
        cls: ObjectClass = .car,
        bearingDeg: Double = 47.0,
        closingMps: Double? = 6.2,
        rangeBand: RangeBand? = .near,
        confidence: Double = 0.81,
        trackId: String = "0:12",
        tCaptureMs: Int64 = Fixture.tCaptureMs,
        ttlMs: Int = 400
    ) throws -> AwarenessEvent {
        try AwarenessEvent(
            source: .x4Cloud,
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

    /// One recorded line `offsetMs` into the walk. Capture and emit advance
    /// **together**, 100 ms apart, because that is what a real recording does:
    /// hold `t_capture_ms` still while the emit stamp runs on and the staleness
    /// gate correctly drops everything after the first TTL.
    static func lineAt(
        offsetMs: Int64,
        trackId: String = "0:12",
        cls: ObjectClass = .car,
        closingMps: Double? = 6.2,
        rangeBand: RangeBand? = .near
    ) throws -> String {
        let event = try event(
            cls: cls,
            closingMps: closingMps,
            rangeBand: rangeBand,
            trackId: trackId,
            tCaptureMs: Fixture.tCaptureMs + offsetMs
        )
        return try line(event, emitMs: Fixture.emitMs + offsetMs)
    }

    /// Exactly what `RecordingEventSink` writes: the wire form plus the
    /// laptop's emit stamp.
    static func line(_ event: AwarenessEvent, emitMs: Int64 = Fixture.emitMs) throws -> String {
        let encoded = try JSONEncoder().encode(event)
        var object = try XCTUnwrap(
            JSONSerialization.jsonObject(with: encoded) as? [String: Any]
        )
        object["t_emit_ms"] = emitMs
        let data = try JSONSerialization.data(withJSONObject: object, options: [.sortedKeys])
        return try XCTUnwrap(String(data: data, encoding: .utf8))
    }
}
