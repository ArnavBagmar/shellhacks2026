import CaneContracts
import Foundation

/// Step 21's additive frame (`contracts/transport_envelope.schema.json`
/// `steer`, `docs/shepherd-steering-plan.md`): the laptop's continuous
/// steering command, one per sensing tick. Shepherd's stage 7 -- their
/// 12-byte BLE packet to a motor -- carried on the WebSocket the phone
/// already holds open, because steering rides the frame we already send.
///
/// Read-only on this side: the laptop owns the geometry and the clock and
/// does all of the arithmetic, so the phone never builds one of these from
/// sensor data. Step 24 is what acts on it (integrator, cue, decay); this
/// file is the shape and its bounds, nothing else.

/// Which sensing path produced a `steer`. Closed at `x4_cloud` because depth
/// only runs in the cloud today (plan §2.1); a local depth path is one case
/// added here and one value added to the schema's enum, never a free string.
public enum SteerSource: String, Sendable, CaseIterable {
    case x4Cloud = "x4_cloud"
}

/// The bounds the contract authors for `steer` alone. Everything a `steer`
/// frame shares with an `AwarenessEvent` -- the capture-time window, the TTL
/// range, the half-open bearing interval -- is read from
/// `AwarenessEventBounds` instead of retyped, so there is still exactly one
/// place each of those numbers lives.
public enum SteerLimits {
    /// Shepherd's stage-5 command, clamped. Positive = right.
    public static let steerMin = -1.0
    public static let steerMax = 1.0
    /// Shepherd's proximityFactor: 0 clear, 1 at the stopping distance.
    public static let proximityMin = 0.0
    public static let proximityMax = 1.0
    /// Plan §3: the most perishable row of the §4.4 staleness table -- a
    /// steer command ages like a car. Step 24 gates staleness on this and
    /// decays the cue to neutral once it expires.
    public static let ttlMs = 400

    static let wireType = "steer"
    static let wireKeys: Set<String> = [
        "type", "t_capture_ms", "ttl_ms", "steer", "proximity",
        "gap_bearing_deg", "blocked", "proximity_anchored", "source",
    ]
}

/// Laptop -> phone. Every field is required; there are no nullable ones,
/// because a steering command with a hole in it is not actionable.
public struct SteerCommand: Sendable, Equatable {
    /// Capture time of the frame this was computed from, PHONE clock, echoed
    /// from the cloud. The phone ages the command against its own clock.
    public let tCaptureMs: Int64
    public let ttlMs: Int
    /// `sign(gapDirection) * sqrt(abs(gapDirection)) * proximity`, clamped to
    /// [-1, 1]. Positive = right.
    public let steer: Double
    /// 0 clear, 1 at the stopping distance. Only a metre-derived number when
    /// `proximityAnchored`; otherwise read it as a band.
    public let proximity: Double
    /// Torso-relative bearing of the chosen gap, 0 ahead, + right.
    public let gapBearingDeg: Double
    /// True when no gap was wide enough to walk through. Distinct from
    /// `steer == 0`, which a clear corridor also produces -- the wearer needs
    /// "nowhere to go" and "nothing in the way" told apart.
    public let blocked: Bool
    /// Whether the ground-plane scale fit behind `proximity` was accepted
    /// (plan §2.3). False on stairs, a slope, or a floor hidden by a crowd:
    /// the same "trust the band, not the metre" rule `RangeTrust` already
    /// applies to phone ranges. Never a reason to drop the frame.
    public let proximityAnchored: Bool
    public let source: SteerSource

    /// Validates every bound, throwing `TransportError.invalidFrame(field)`
    /// named for the *wire* key so a rejection reads the same in both
    /// languages' logs. Rejected, not trapped: a bad frame from the laptop
    /// must never take the phone down mid-walk.
    public init(
        tCaptureMs: Int64,
        ttlMs: Int,
        steer: Double,
        proximity: Double,
        gapBearingDeg: Double,
        blocked: Bool,
        proximityAnchored: Bool,
        source: SteerSource
    ) throws {
        guard tCaptureMs >= Int64(AwarenessEventBounds.tCaptureMsMin),
              tCaptureMs <= Int64(AwarenessEventBounds.tCaptureMsMax)
        else {
            throw TransportError.invalidFrame("t_capture_ms")
        }
        guard (AwarenessEventBounds.ttlMsMin...AwarenessEventBounds.ttlMsMax).contains(ttlMs) else {
            throw TransportError.invalidFrame("ttl_ms")
        }
        guard steer.isFinite, steer >= SteerLimits.steerMin, steer <= SteerLimits.steerMax else {
            throw TransportError.invalidFrame("steer")
        }
        guard proximity.isFinite,
              proximity >= SteerLimits.proximityMin,
              proximity <= SteerLimits.proximityMax
        else {
            throw TransportError.invalidFrame("proximity")
        }
        // Half-open (-180, 180], exactly as AwarenessEvent.bearing_deg: atan2
        // returns -180 for directly behind, so a producer that forgets to
        // normalize by adding 360 is rejected here rather than silently
        // steering the wearer into the one bearing the contract excludes.
        guard gapBearingDeg.isFinite,
              gapBearingDeg > AwarenessEventBounds.bearingDegMinExclusive,
              gapBearingDeg <= AwarenessEventBounds.bearingDegMax
        else {
            throw TransportError.invalidFrame("gap_bearing_deg")
        }
        self.tCaptureMs = tCaptureMs
        self.ttlMs = ttlMs
        self.steer = steer
        self.proximity = proximity
        self.gapBearingDeg = gapBearingDeg
        self.blocked = blocked
        self.proximityAnchored = proximityAnchored
        self.source = source
    }

    /// Reads one `steer` frame straight from the wire.
    public static func decode(_ data: Data) throws -> SteerCommand {
        try decode(StrictObject(data: data, frameType: SteerLimits.wireType, keys: SteerLimits.wireKeys))
    }

    /// The `InboundMessage` path, where the envelope has already read `type`.
    static func decode(_ raw: [String: Any]) throws -> SteerCommand {
        try decode(StrictObject(object: raw, keys: SteerLimits.wireKeys))
    }

    private static func decode(_ object: StrictObject) throws -> SteerCommand {
        guard let source = SteerSource(rawValue: try object.string("source")) else {
            throw TransportError.invalidFrame("source")
        }
        return try SteerCommand(
            tCaptureMs: try object.integer("t_capture_ms"),
            ttlMs: Int(try object.integer("ttl_ms")),
            steer: try object.double("steer"),
            proximity: try object.double("proximity"),
            gapBearingDeg: try object.double("gap_bearing_deg"),
            blocked: try object.boolean("blocked"),
            proximityAnchored: try object.boolean("proximity_anchored"),
            source: source
        )
    }

    /// The phone never sends `steer`; this exists so the golden fixture can
    /// prove both languages round-trip the same frame, the mirror image of
    /// `SceneQuery.decode`'s reason for existing.
    var jsonObject: [String: Any] {
        [
            "type": SteerLimits.wireType,
            "t_capture_ms": tCaptureMs,
            "ttl_ms": ttlMs,
            "steer": steer,
            "proximity": proximity,
            "gap_bearing_deg": gapBearingDeg,
            "blocked": blocked,
            "proximity_anchored": proximityAnchored,
            "source": source.rawValue,
        ]
    }

    func encoded() throws -> Data {
        try JSONSerialization.data(withJSONObject: jsonObject)
    }
}
