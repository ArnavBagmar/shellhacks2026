import Foundation

/// Where a hand guide is. The phone decides; the watch only renders it.
public enum GuideState: String, Sendable, Equatable, CaseIterable {
    /// Gemini is finding the thing.
    case looking
    /// The thing is tracked but no hand is in view yet.
    case findHand
    /// A hand and the thing are both in view: the watch taps.
    case guiding
    case reached
    case lost
    case stopped

    /// The guide is over; the watch stops tapping.
    public var isFinal: Bool { self == .reached || self == .lost || self == .stopped }
}

/// Phone -> watch, many times a second, over WatchConnectivity. Plain
/// property-list types only (`WCSession.sendMessage` rejects anything else).
public struct GuideMessage: Sendable, Equatable {
    public static let version = 1

    public var state: GuideState
    /// Fingertip to the thing's surface, metres; nil when there is no hand.
    public var gapM: Double?
    /// False when the gap is only an estimate (no TrueDepth reading): the
    /// watch then shows no number, only the taps.
    public var metric: Bool
    public var target: String
    /// "Left.", "Down." ... what the phone last said, for the watch face.
    public var hint: String?
    /// Phone wall clock, ms: the watch goes quiet if messages stop.
    public var sentMs: Int64

    public init(state: GuideState, gapM: Double? = nil, metric: Bool = true, target: String,
                hint: String? = nil, sentMs: Int64) {
        self.state = state
        self.gapM = gapM
        self.metric = metric
        self.target = target
        self.hint = hint
        self.sentMs = sentMs
    }

    public var dictionary: [String: Any] {
        var out: [String: Any] = [
            "v": Self.version, "kind": "guide", "state": state.rawValue, "metric": metric,
            "target": target, "t": sentMs,
        ]
        if let gapM { out["gap"] = gapM }
        if let hint { out["hint"] = hint }
        return out
    }

    public init?(dictionary d: [String: Any]) {
        guard d["kind"] as? String == "guide", (d["v"] as? Int) == Self.version,
              let raw = d["state"] as? String, let state = GuideState(rawValue: raw),
              let target = d["target"] as? String
        else { return nil }
        let sent = (d["t"] as? Int64) ?? (d["t"] as? Int).map(Int64.init) ?? (d["t"] as? Double).map { Int64($0) }
        guard let sent else { return nil }
        self.init(state: state, gapM: d["gap"] as? Double, metric: d["metric"] as? Bool ?? false,
                  target: target, hint: d["hint"] as? String, sentMs: sent)
    }

    /// "12 cm" for the watch face, or nil when there is nothing honest to show.
    public var distanceText: String? {
        guard metric, let gapM else { return nil }
        let cm = Int((gapM * 100).rounded())
        return cm < 100 ? "\(cm) cm" : String(format: "%.1f m", gapM)
    }
}
