import CaneContracts
import Foundation

/// The frame shapes the phone reads from the laptop (D2). The phone never
/// sends `event` itself, so this only decodes what it might receive.
public enum InboundMessage: Equatable, Sendable {
    case ping(t1: Int64)
    case event(AwarenessEvent)
    case answer(SceneAnswer)
    /// The natural-voice audio for one `SpeakRequest`.
    case voice(VoiceClip)
    case unknown(type: String)

    /// Decodes one wire frame. Throws `TransportError.malformed` for bad
    /// JSON or a missing/malformed field, `TransportError.invalidEvent`
    /// when an `event` frame's nested payload fails contract validation,
    /// `TransportError.invalidFrame` for a bad `answer` frame (step 20).
    /// An unrecognised (but well-formed) `type` -- including the reserved
    /// `health`/`location` -- decodes to `.unknown` rather than throwing.
    public static func decode(_ data: Data) throws -> InboundMessage {
        guard
            let raw = try? JSONSerialization.jsonObject(with: data),
            let object = raw as? [String: Any],
            let type = object["type"] as? String
        else {
            throw TransportError.malformed
        }

        switch type {
        case "ping":
            return try decodePing(object)
        case "event":
            return try decodeEvent(object)
        case "answer":
            return .answer(try SceneAnswer.decode(object))
        case "voice":
            return .voice(try VoiceClip.decode(object))
        default:
            return .unknown(type: type)
        }
    }

    private static func decodePing(_ object: [String: Any]) throws -> InboundMessage {
        guard let t1 = (object["t1"] as? NSNumber)?.int64Value else {
            throw TransportError.malformed
        }
        return .ping(t1: t1)
    }

    private static func decodeEvent(_ object: [String: Any]) throws -> InboundMessage {
        guard
            let eventObject = object["event"] as? [String: Any],
            let eventData = try? JSONSerialization.data(withJSONObject: eventObject)
        else {
            throw TransportError.malformed
        }
        do {
            return .event(try AwarenessEvent.decode(eventData))
        } catch let error as AwarenessEventError {
            throw TransportError.invalidEvent(error)
        }
    }
}
