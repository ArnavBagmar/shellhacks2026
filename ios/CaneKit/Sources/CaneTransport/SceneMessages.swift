import Foundation

/// Step 20's two additive frames (`contracts/transport_envelope.schema.json`
/// `query` / `answer`): the phone asks "is there a bathroom around?", the
/// laptop answers once per `queryId`. Same rules as the event contract:
/// every key present (nullable ones as explicit `null`), unknown keys
/// rejected, and a bad value is a thrown `TransportError`, never a trap.

public enum SceneQueryKind: String, Sendable, CaseIterable {
    case find
    case describeAround = "describe_around"
    case describeFront = "describe_front"
    /// Any other question, answered in a sentence.
    case ask
}

public enum SceneAnswerStatus: String, Sendable, CaseIterable {
    case found
    case notFound = "not_found"
    case described
    case unavailable
}

public enum SceneConfidence: String, Sendable, CaseIterable {
    case low, medium, high
}

/// One front-camera JPEG. Mirroring is off on capture, so image-left is the
/// wearer's left.
public struct PhoneFrame: Sendable, Equatable {
    public let jpegBase64: String
    public let hfovDeg: Double
    public let width: Int
    public let height: Int

    public init(jpeg: Data, hfovDeg: Double, width: Int, height: Int) throws {
        try self.init(jpegBase64: jpeg.base64EncodedString(), hfovDeg: hfovDeg, width: width, height: height)
    }

    init(jpegBase64: String, hfovDeg: Double, width: Int, height: Int) throws {
        guard (4...SceneLimits.maxJPEGBase64Chars).contains(jpegBase64.utf8.count) else {
            throw TransportError.invalidFrame("phone_frame.jpeg_b64")
        }
        guard hfovDeg.isFinite, hfovDeg > 0, hfovDeg < 180 else {
            throw TransportError.invalidFrame("phone_frame.hfov_deg")
        }
        guard (1...8192).contains(width), (1...8192).contains(height) else {
            throw TransportError.invalidFrame("phone_frame.size")
        }
        self.jpegBase64 = jpegBase64
        self.hfovDeg = hfovDeg
        self.width = width
        self.height = height
    }

    var jsonObject: [String: Any] {
        ["jpeg_b64": jpegBase64, "hfov_deg": hfovDeg, "width": width, "height": height]
    }
}

/// Phone -> laptop.
public struct SceneQuery: Sendable, Equatable {
    public let queryId: String
    public let kind: SceneQueryKind
    public let target: String?
    public let utterance: String
    public let tPhoneMs: Int64
    public let phoneFrame: PhoneFrame?

    public init(
        queryId: String,
        kind: SceneQueryKind,
        target: String?,
        utterance: String,
        tPhoneMs: Int64,
        phoneFrame: PhoneFrame?
    ) throws {
        try SceneLimits.checkQueryId(queryId)
        if let target, !(1...60).contains(target.count) {
            throw TransportError.invalidFrame("target")
        }
        guard (kind == .find) == (target != nil) else {
            throw TransportError.invalidFrame("target")
        }
        guard utterance.count <= 300 else { throw TransportError.invalidFrame("utterance") }
        self.queryId = queryId
        self.kind = kind
        self.target = target
        self.utterance = utterance
        self.tPhoneMs = tPhoneMs
        self.phoneFrame = phoneFrame
    }

    /// The wire frame, `type` included.
    public func encoded() throws -> Data {
        let object: [String: Any] = [
            "type": "query",
            "query_id": queryId,
            "kind": kind.rawValue,
            "target": target ?? NSNull(),
            "utterance": utterance,
            "t_phone_ms": tPhoneMs,
            "phone_frame": phoneFrame?.jsonObject ?? NSNull(),
        ]
        return try JSONSerialization.data(withJSONObject: object)
    }

    /// Reads a `query` frame. The phone never receives one; this exists so
    /// the golden fixture proves both languages accept the same frames.
    public static func decode(_ data: Data) throws -> SceneQuery {
        let object = try StrictObject(data: data, frameType: "query", keys: [
            "type", "query_id", "kind", "target", "utterance", "t_phone_ms", "phone_frame",
        ])
        guard let kind = SceneQueryKind(rawValue: try object.string("kind")) else {
            throw TransportError.invalidFrame("kind")
        }
        var frame: PhoneFrame?
        if let raw = try object.optionalObject("phone_frame") {
            let nested = try StrictObject(object: raw, keys: ["jpeg_b64", "hfov_deg", "width", "height"])
            frame = try PhoneFrame(
                jpegBase64: try nested.string("jpeg_b64"),
                hfovDeg: try nested.double("hfov_deg"),
                width: Int(try nested.integer("width")),
                height: Int(try nested.integer("height"))
            )
        }
        return try SceneQuery(
            queryId: try object.string("query_id"),
            kind: kind,
            target: try object.optionalString("target"),
            utterance: try object.string("utterance"),
            tPhoneMs: try object.integer("t_phone_ms"),
            phoneFrame: frame
        )
    }
}

/// Laptop -> phone. Direction travels only as `bearingDeg` (torso-relative,
/// 0 ahead, negative left), so the phone speaks it in the same words it
/// uses for hazards.
public struct SceneAnswer: Sendable, Equatable {
    public let queryId: String
    public let status: SceneAnswerStatus
    public let subject: String?
    public let bearingDeg: Double?
    public let detail: String?
    public let confidence: SceneConfidence?
    public let latencyMs: Int64

    public init(
        queryId: String,
        status: SceneAnswerStatus,
        subject: String?,
        bearingDeg: Double?,
        detail: String?,
        confidence: SceneConfidence?,
        latencyMs: Int64
    ) throws {
        try SceneLimits.checkQueryId(queryId)
        if let subject, !(1...80).contains(subject.count) { throw TransportError.invalidFrame("subject") }
        if let detail, !(1...240).contains(detail.count) { throw TransportError.invalidFrame("detail") }
        if let bearingDeg, !(bearingDeg.isFinite && bearingDeg > -180 && bearingDeg <= 180) {
            throw TransportError.invalidFrame("bearing_deg")
        }
        guard latencyMs >= 0 else { throw TransportError.invalidFrame("latency_ms") }
        if status == .found, subject == nil { throw TransportError.invalidFrame("subject") }
        if status == .described, detail == nil { throw TransportError.invalidFrame("detail") }
        if status != .found, bearingDeg != nil { throw TransportError.invalidFrame("bearing_deg") }
        self.queryId = queryId
        self.status = status
        self.subject = subject
        self.bearingDeg = bearingDeg
        self.detail = detail
        self.confidence = confidence
        self.latencyMs = latencyMs
    }

    static func decode(_ raw: [String: Any]) throws -> SceneAnswer {
        let object = try StrictObject(object: raw, keys: [
            "type", "query_id", "status", "subject", "bearing_deg", "detail", "confidence", "latency_ms",
        ])
        guard let status = SceneAnswerStatus(rawValue: try object.string("status")) else {
            throw TransportError.invalidFrame("status")
        }
        var confidence: SceneConfidence?
        if let raw = try object.optionalString("confidence") {
            guard let parsed = SceneConfidence(rawValue: raw) else {
                throw TransportError.invalidFrame("confidence")
            }
            confidence = parsed
        }
        return try SceneAnswer(
            queryId: try object.string("query_id"),
            status: status,
            subject: try object.optionalString("subject"),
            bearingDeg: try object.optionalDouble("bearing_deg"),
            detail: try object.optionalString("detail"),
            confidence: confidence,
            latencyMs: try object.integer("latency_ms")
        )
    }
}

/// Phone -> laptop: "say this in the natural voice" (answers and prompts
/// only; hazards never leave the phone).
public struct SpeakRequest: Sendable, Equatable {
    public static let maxTextChars = 400
    public let speakId: String
    public let text: String

    public init(speakId: String, text: String) throws {
        try SceneLimits.checkQueryId(speakId)
        guard (1...Self.maxTextChars).contains(text.count) else { throw TransportError.invalidFrame("text") }
        self.speakId = speakId
        self.text = text
    }

    public func encoded() throws -> Data {
        try JSONSerialization.data(withJSONObject: ["type": "speak", "speak_id": speakId, "text": text])
    }

    /// The phone never receives one; this exists for the golden fixture.
    public static func decode(_ data: Data) throws -> SpeakRequest {
        let object = try StrictObject(data: data, frameType: "speak", keys: ["type", "speak_id", "text"])
        return try SpeakRequest(speakId: try object.string("speak_id"), text: try object.string("text"))
    }
}

public enum VoiceClipStatus: String, Sendable {
    case ok, unavailable
}

/// Laptop -> phone: the audio for one `SpeakRequest`, or `unavailable`.
public struct VoiceClip: Sendable, Equatable {
    public let speakId: String
    public let status: VoiceClipStatus
    /// ElevenLabs `output_format`, e.g. "mp3_44100_64".
    public let format: String?
    public let audio: Data?
    public let latencyMs: Int64

    public init(speakId: String, status: VoiceClipStatus, format: String?, audio: Data?, latencyMs: Int64) throws {
        try SceneLimits.checkQueryId(speakId)
        guard (status == .ok) == (format != nil), (status == .ok) == (audio != nil) else {
            throw TransportError.invalidFrame("audio_b64")
        }
        if let format, !(1...40).contains(format.count) { throw TransportError.invalidFrame("format") }
        guard latencyMs >= 0 else { throw TransportError.invalidFrame("latency_ms") }
        self.speakId = speakId
        self.status = status
        self.format = format
        self.audio = audio
        self.latencyMs = latencyMs
    }

    static func decode(_ raw: [String: Any]) throws -> VoiceClip {
        let object = try StrictObject(object: raw, keys: [
            "type", "speak_id", "status", "format", "audio_b64", "latency_ms",
        ])
        guard let status = VoiceClipStatus(rawValue: try object.string("status")) else {
            throw TransportError.invalidFrame("status")
        }
        var audio: Data?
        if let b64 = try object.optionalString("audio_b64") {
            guard (4...SceneLimits.maxJPEGBase64Chars).contains(b64.utf8.count),
                  let decoded = Data(base64Encoded: b64)
            else { throw TransportError.invalidFrame("audio_b64") }
            audio = decoded
        }
        return try VoiceClip(
            speakId: try object.string("speak_id"), status: status,
            format: try object.optionalString("format"), audio: audio,
            latencyMs: try object.integer("latency_ms")
        )
    }
}

enum SceneLimits {
    static let maxJPEGBase64Chars = 2_800_000

    static func checkQueryId(_ id: String) throws {
        let allowed = CharacterSet(charactersIn: "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789_-")
        guard (1...64).contains(id.unicodeScalars.count),
              id.unicodeScalars.allSatisfy(allowed.contains)
        else {
            throw TransportError.invalidFrame("query_id")
        }
    }
}

/// `additionalProperties: false` + "every key present" + JSON-number
/// strictness (a bool is not an integer, 1.5 is not an integer) over a
/// `JSONSerialization` object.
struct StrictObject {
    private let object: [String: Any]

    init(data: Data, frameType: String, keys: Set<String>) throws {
        guard let object = (try? JSONSerialization.jsonObject(with: data)) as? [String: Any],
              object["type"] as? String == frameType
        else {
            throw TransportError.malformed
        }
        try self.init(object: object, keys: keys)
    }

    init(object: [String: Any], keys: Set<String>) throws {
        let present = Set(object.keys)
        if let unknown = present.subtracting(keys).sorted().first {
            throw TransportError.invalidFrame(unknown)
        }
        if let missing = keys.subtracting(present).sorted().first {
            throw TransportError.invalidFrame(missing)
        }
        self.object = object
    }

    private func isNull(_ key: String) -> Bool { object[key] is NSNull }

    private func number(_ key: String) throws -> NSNumber {
        guard let number = object[key] as? NSNumber, !Self.isBool(number) else {
            throw TransportError.invalidFrame(key)
        }
        return number
    }

    private static func isBool(_ number: NSNumber) -> Bool {
        CFGetTypeID(number) == CFBooleanGetTypeID()
    }

    func string(_ key: String) throws -> String {
        guard let value = object[key] as? String else { throw TransportError.invalidFrame(key) }
        return value
    }

    func optionalString(_ key: String) throws -> String? {
        isNull(key) ? nil : try string(key)
    }

    func integer(_ key: String) throws -> Int64 {
        let number = try number(key)
        let double = number.doubleValue
        guard double.isFinite, double.rounded() == double, let value = Int64(exactly: double) else {
            throw TransportError.invalidFrame(key)
        }
        if CFNumberIsFloatType(number) {
            // "1.0" on the wire: JSON Schema's "integer" accepts it, but
            // Python's strict decoder does not -- be strict like Python.
            throw TransportError.invalidFrame(key)
        }
        return value
    }

    /// JSON's `true`/`false` and nothing else: `1` is a number, not a flag,
    /// and a producer that sends one has a bug the phone must not paper over
    /// (step 21's `blocked` / `proximity_anchored`).
    func boolean(_ key: String) throws -> Bool {
        guard let number = object[key] as? NSNumber, Self.isBool(number) else {
            throw TransportError.invalidFrame(key)
        }
        return number.boolValue
    }

    func double(_ key: String) throws -> Double {
        let value = try number(key).doubleValue
        guard value.isFinite else { throw TransportError.invalidFrame(key) }
        return value
    }

    func optionalDouble(_ key: String) throws -> Double? {
        isNull(key) ? nil : try double(key)
    }

    func optionalObject(_ key: String) throws -> [String: Any]? {
        if isNull(key) { return nil }
        guard let value = object[key] as? [String: Any] else { throw TransportError.invalidFrame(key) }
        return value
    }
}
