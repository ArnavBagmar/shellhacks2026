import Foundation

/// Phone -> laptop, ~8 times a second while "guide my hand" runs: where the
/// phone has the thing and the fingertip, and the gap between them, plus a
/// small camera frame a few times a second -- so the laptop page can show
/// the live tracking. Advisory and display-only: the laptop never acts on it.
public struct GuideUpdate: Sendable, Equatable {
    public static let states: Set<String> = ["looking", "find_hand", "guiding", "reached", "lost", "stopped"]

    public struct Box: Sendable, Equatable {
        public let xMin, yMin, xMax, yMax: Double
        public init(xMin: Double, yMin: Double, xMax: Double, yMax: Double) {
            (self.xMin, self.yMin, self.xMax, self.yMax) = (xMin, yMin, xMax, yMax)
        }
    }

    public let tPhoneMs: Int64
    public let state: String
    public let target: String
    /// The thing, photo fractions from the top-left; nil when not tracked.
    public let box: Box?
    /// The fingertip nearest the thing, photo fractions; nil with no hand.
    public let hand: (x: Double, y: Double)?
    public let gapM: Double?
    public let metric: Bool
    public let hint: String?
    public let frame: PhoneFrame?

    public init(tPhoneMs: Int64, state: String, target: String, box: Box?, hand: (x: Double, y: Double)?,
                gapM: Double?, metric: Bool, hint: String?, frame: PhoneFrame?) throws {
        guard Self.states.contains(state) else { throw TransportError.invalidFrame("state") }
        guard (1...60).contains(target.count) else { throw TransportError.invalidFrame("target") }
        if let box {
            for v in [box.xMin, box.yMin, box.xMax, box.yMax] where !(v.isFinite && (0...1).contains(v)) {
                throw TransportError.invalidFrame("box")
            }
            guard box.xMin < box.xMax, box.yMin < box.yMax else { throw TransportError.invalidFrame("box") }
        }
        if let hand, !(hand.x.isFinite && hand.y.isFinite && (0...1).contains(hand.x) && (0...1).contains(hand.y)) {
            throw TransportError.invalidFrame("hand")
        }
        if let gapM, !(gapM.isFinite && gapM >= 0) { throw TransportError.invalidFrame("gap_m") }
        if let hint, !(1...40).contains(hint.count) { throw TransportError.invalidFrame("hint") }
        self.tPhoneMs = tPhoneMs
        self.state = state
        self.target = target
        self.box = box
        self.hand = hand
        self.gapM = gapM.map { min($0, 10) }
        self.metric = metric
        self.hint = hint
        self.frame = frame
    }

    public static func == (a: GuideUpdate, b: GuideUpdate) -> Bool {
        a.tPhoneMs == b.tPhoneMs && a.state == b.state && a.target == b.target && a.box == b.box
            && a.hand?.x == b.hand?.x && a.hand?.y == b.hand?.y && a.gapM == b.gapM && a.metric == b.metric
            && a.hint == b.hint && a.frame == b.frame
    }

    /// The wire frame, `type` included. Every key is present; empty ones are
    /// explicit `null`, as in every other frame.
    public var jsonObject: [String: Any] {
        [
            "type": "guide",
            "t_phone_ms": tPhoneMs,
            "state": state,
            "target": target,
            "box": box.map { ["x_min": $0.xMin, "y_min": $0.yMin, "x_max": $0.xMax, "y_max": $0.yMax] } ?? NSNull(),
            "hand": hand.map { ["x": $0.x, "y": $0.y] } ?? NSNull(),
            "gap_m": gapM ?? NSNull(),
            "metric": metric,
            "hint": hint ?? NSNull(),
            "frame": frame?.jsonObject ?? NSNull(),
        ]
    }

    public func encoded() throws -> Data {
        try JSONSerialization.data(withJSONObject: jsonObject)
    }
}
