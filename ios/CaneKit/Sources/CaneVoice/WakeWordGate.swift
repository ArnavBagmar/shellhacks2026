import Foundation

/// Open mic, but the phone only acts on speech addressed to it: "Hey Cane,
/// <command>". Pure and clock-injected, so every timing rule is a test.
///
/// Feed it every transcription result (volatile and final) and call `tick`
/// a few times a second. It emits a command when the recognizer finalizes
/// the segment, or when the text has been stable for `stableMs` -- whichever
/// comes first -- so the wearer is not left waiting on a slow finalization.
/// "Hey Cane" alone arms the gate for `armedMs`: the next thing said is the
/// command, no wake word needed.
public struct WakeWordGate: Sendable {
    public enum Output: Equatable, Sendable {
        case none
        /// The wake word with nothing after it: answer "Yes?" and listen.
        case wakeOnly
        case command(String)
    }

    /// Silence before acting on an *unfinished* sentence ("is there a..."):
    /// long enough for a mid-sentence pause; 1.2 s cut sentences short live.
    public static let stableMs: Int64 = 1_800
    /// Silence before acting on a sentence that already parses as a command.
    /// Live testing felt slow waiting the full 1.8 s after "is there a
    /// bathroom around" when the recognizer was late to finalize.
    public static let completeStableMs: Int64 = 700
    public static let wakeOnlyStableMs: Int64 = 1_500
    /// Short, because while armed *any* nearby speech counts as a command.
    public static let armedMs: Int64 = 5_000
    /// "Yes?" waits this long after a bare "Hey Cane": the recognizer often
    /// finalizes the wake word as its own segment while the command is
    /// still being said, and answering at once would mute the mic over it.
    public static let wakePromptDelayMs: Int64 = 1_200

    static let greetings: Set<String> = ["hey", "hi", "okay", "ok", "hay", "yo"]
    static let names: Set<String> = ["cane", "kane", "cain", "caine", "kain", "cayne", "kayne", "canes"]

    private var pending: String?
    private var lastChangeMs: Int64 = 0
    private var armedUntilMs: Int64?
    private var wakePromptDueMs: Int64?
    /// Set after a stability emit, so the rest of that segment (including
    /// its eventual final result) is not emitted a second time.
    private var consumedSegment = false

    /// Whether a command is already complete (defaults to "it parses").
    private let isComplete: @Sendable (String) -> Bool

    public init(isComplete: @escaping @Sendable (String) -> Bool = { IntentParser.parse($0) != nil }) {
        self.isComplete = isComplete
    }

    public mutating func feed(text: String, isFinal: Bool, nowMs: Int64) -> Output {
        if consumedSegment {
            if isFinal { consumedSegment = false }
            return .none
        }
        let words = IntentParser.normalize(text).split(separator: " ").map(String.init)
        let command: [String]
        if let wake = Self.lastWakeIndex(words) {
            command = Array(words[(wake + 2)...])
        } else if let armed = armedUntilMs, nowMs < armed, !words.isEmpty {
            command = words
        } else {
            if isFinal { pending = nil }
            return .none
        }
        let joined = command.joined(separator: " ")
        if !joined.isEmpty { wakePromptDueMs = nil }
        if isFinal {
            pending = nil
            if joined.isEmpty {
                armedUntilMs = nowMs + Self.armedMs
                wakePromptDueMs = nowMs + Self.wakePromptDelayMs
                return .none
            }
            armedUntilMs = nil
            return .command(joined)
        }
        if joined != pending {
            pending = joined
            lastChangeMs = nowMs
        }
        return .none
    }

    public mutating func tick(nowMs: Int64) -> Output {
        if let due = wakePromptDueMs, pending == nil, nowMs >= due {
            wakePromptDueMs = nil
            return .wakeOnly
        }
        guard let text = pending else { return .none }
        let waited = nowMs - lastChangeMs
        if text.isEmpty, waited >= Self.wakeOnlyStableMs {
            pending = nil
            consumedSegment = true
            armedUntilMs = nowMs + Self.armedMs
            return .wakeOnly
        }
        if !text.isEmpty, waited >= (isComplete(text) ? Self.completeStableMs : Self.stableMs) {
            pending = nil
            consumedSegment = true
            armedUntilMs = nil
            return .command(text)
        }
        return .none
    }

    /// The next utterance is a command without the wake word -- after "Sorry,
    /// I didn't catch that", so the wearer can just say it again.
    public mutating func arm(nowMs: Int64) {
        armedUntilMs = nowMs + Self.armedMs
    }

    /// Forget everything in flight -- e.g. the phone just started speaking.
    public mutating func reset() {
        pending = nil
        consumedSegment = false
    }

    static func lastWakeIndex(_ words: [String]) -> Int? {
        guard words.count >= 2 else { return nil }
        for i in stride(from: words.count - 2, through: 0, by: -1)
        where greetings.contains(words[i]) && names.contains(words[i + 1]) {
            return i
        }
        return nil
    }
}
