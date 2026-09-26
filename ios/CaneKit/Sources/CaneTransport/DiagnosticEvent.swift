import Foundation

/// One line of the phone's black-box recorder, sent as a `diag` frame so
/// the laptop can keep it (`logs/phone-YYYYMMDD.jsonl`). Text and numbers
/// only -- never audio.
public struct DiagnosticEvent: Sendable, Equatable {
    public enum Value: Sendable, Equatable {
        case text(String)
        case number(Double)
        case flag(Bool)

        var json: Any {
            switch self {
            case .text(let s): return String(s.prefix(DiagnosticEvent.maxValueChars))
            case .number(let n): return n.isFinite ? n : 0
            case .flag(let b): return b
            }
        }
    }

    public static let maxFields = 24
    public static let maxValueChars = 500

    public let tPhoneMs: Int64
    public let kind: String
    public let data: [String: Value]

    public init(tPhoneMs: Int64, kind: String, data: [String: Value] = [:]) {
        self.tPhoneMs = tPhoneMs
        self.kind = kind
        self.data = data
    }

    /// The JSON object without `type` (also what the phone's own file holds).
    public var jsonObject: [String: Any] {
        let fields = data.sorted { $0.key < $1.key }.prefix(Self.maxFields)
        return [
            "t_phone_ms": tPhoneMs,
            "kind": kind,
            "data": Dictionary(uniqueKeysWithValues: fields.map { ($0.key, $0.value.json) }),
        ]
    }

    public func encoded() throws -> Data {
        var object = jsonObject
        object["type"] = "diag"
        return try JSONSerialization.data(withJSONObject: object)
    }
}
