import CaneContracts
import Foundation

/// Step 29's additive frame (`contracts/transport_envelope.schema.json`
/// `ground`): a descending discontinuity in the floor ahead.
///
/// `camera-sync-design.md` §2.3 gave drop-off detection up because YOLO cannot
/// box a hole. This is the door that section left open -- a deviation from the
/// ground plane step 27 already fits -- and it arrives on its own frame rather
/// than as an `AwarenessEvent` for a concrete reason: the urgency ladder scores
/// on metric range or closing speed, and a hole has neither, so every ground
/// hazard would score urgency 1 and never be spoken.
///
/// Read-only on this side. The laptop owns the geometry, the fit and the
/// confidence floor; the phone owns when to say it and in what words.
public enum GroundSource: String, Sendable, CaseIterable {
    case x4Cloud = "x4_cloud"
}

public enum GroundLimits {
    /// A hole does not move, but the wearer does: 1 s is ~1.4 m of walking.
    public static let ttlMs = 1000
    /// Below this the laptop would not have sent the frame; asserted here too
    /// so a hand-rolled frame cannot get round the floor §2.3 demands.
    public static let minDropRatio = 1.0
    public static let confidenceMin = 0.0
    public static let confidenceMax = 1.0
    public static let spanDegMin = 0.0
    public static let spanDegMax = 360.0

    static let wireType = "ground"
    static let wireKeys: Set<String> = [
        "type", "t_capture_ms", "ttl_ms", "bearing_deg", "span_deg",
        "drop_ratio", "confidence", "source",
    ]
}

/// Laptop -> phone. Every field is required: a ground cue with a hole in it is
/// not actionable, and this one is about a hole already.
public struct GroundCue: Sendable, Equatable {
    public let tCaptureMs: Int64
    public let ttlMs: Int
    /// Torso-relative, 0 ahead, + right -- the same convention as
    /// `AwarenessEvent.bearing_deg`.
    public let bearingDeg: Double
    /// How wide the discontinuity is, in degrees.
    public let spanDeg: Double
    /// Recovered distance over flat-floor distance. **A ratio, never a depth.**
    /// Depth Anything V2 is affine-invariant in disparity, measured, so nothing
    /// in this system knows how deep the hole is -- only that the floor there
    /// reads this many times farther than it should. Nothing may render it as
    /// centimetres.
    public let dropRatio: Double
    public let confidence: Double
    public let source: GroundSource

    public init(
        tCaptureMs: Int64,
        ttlMs: Int,
        bearingDeg: Double,
        spanDeg: Double,
        dropRatio: Double,
        confidence: Double,
        source: GroundSource
    ) throws {
        guard tCaptureMs >= Int64(AwarenessEventBounds.tCaptureMsMin),
              tCaptureMs <= Int64(AwarenessEventBounds.tCaptureMsMax)
        else { throw TransportError.invalidFrame("t_capture_ms") }
        guard (AwarenessEventBounds.ttlMsMin...AwarenessEventBounds.ttlMsMax).contains(ttlMs) else {
            throw TransportError.invalidFrame("ttl_ms")
        }
        // Half-open (-180, 180], exactly as AwarenessEvent.bearing_deg.
        guard bearingDeg.isFinite,
              bearingDeg > AwarenessEventBounds.bearingDegMinExclusive,
              bearingDeg <= AwarenessEventBounds.bearingDegMax
        else { throw TransportError.invalidFrame("bearing_deg") }
        guard spanDeg.isFinite,
              spanDeg > GroundLimits.spanDegMin,
              spanDeg <= GroundLimits.spanDegMax
        else { throw TransportError.invalidFrame("span_deg") }
        guard dropRatio.isFinite, dropRatio > GroundLimits.minDropRatio else {
            throw TransportError.invalidFrame("drop_ratio")
        }
        guard confidence.isFinite,
              confidence >= GroundLimits.confidenceMin,
              confidence <= GroundLimits.confidenceMax
        else { throw TransportError.invalidFrame("confidence") }

        self.tCaptureMs = tCaptureMs
        self.ttlMs = ttlMs
        self.bearingDeg = bearingDeg
        self.spanDeg = spanDeg
        self.dropRatio = dropRatio
        self.confidence = confidence
        self.source = source
    }

    public static func decode(_ data: Data) throws -> GroundCue {
        try decode(StrictObject(data: data, frameType: GroundLimits.wireType, keys: GroundLimits.wireKeys))
    }

    static func decode(_ raw: [String: Any]) throws -> GroundCue {
        try decode(StrictObject(object: raw, keys: GroundLimits.wireKeys))
    }

    private static func decode(_ object: StrictObject) throws -> GroundCue {
        guard let source = GroundSource(rawValue: try object.string("source")) else {
            throw TransportError.invalidFrame("source")
        }
        return try GroundCue(
            tCaptureMs: try object.integer("t_capture_ms"),
            ttlMs: Int(try object.integer("ttl_ms")),
            bearingDeg: try object.double("bearing_deg"),
            spanDeg: try object.double("span_deg"),
            dropRatio: try object.double("drop_ratio"),
            confidence: try object.double("confidence"),
            source: source
        )
    }
}
