import CaneContracts
import Foundation

/// The frame shapes the phone reads from the laptop (D2). The phone never
/// sends `event` itself, so this only decodes what it might receive.
public enum InboundMessage: Equatable, Sendable {
    case ping(t1: Int64)
    case event(AwarenessEvent)
    case answer(SceneAnswer)
    case steer(SteerCommand)
    case ground(GroundCue)
    /// The natural-voice audio for one `SpeakRequest`.
    case voice(VoiceClip)
    case unknown(type: String)

    /// Decodes one wire frame. Throws `TransportError.malformed` for bad
    /// JSON or a missing/malformed field, `TransportError.invalidEvent`
    /// when an `event` frame's nested payload fails contract validation,
    /// `TransportError.invalidFrame` for a bad `answer` (step 20) or `steer`
    /// (step 21) frame. An unrecognised (but well-formed) `type` -- including
    /// the reserved `health`/`location` -- decodes to `.unknown` rather than
    /// throwing, which is what lets this phone keep working against a laptop
    /// that has learned a frame shape it has not (plan §4.1).
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
        case "steer":
            return .steer(try SteerCommand.decode(object))
        case "ground":
            return .ground(try GroundCue.decode(object))
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
