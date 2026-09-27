import Foundation

/// Open mic, but the phone only acts on speech addressed to it: "Hey Cane,
/// <command>". Pure and clock-injected, so every timing rule is a test.
///
/// Feed it every transcription result (volatile and final) and call `tick`
/// a few times a second. It emits a command when the recognizer finalizes
/// the segment -- live logs (2026-09-26) show the final lands ~40 ms after
/// the last volatile result, so that is normally at once -- or, if the final
/// is late, when the text has been stable for `stableMs`. If the final then
/// says more than what was emitted early ("how far is the sprite" -> "... the
/// sprite can"), the final is emitted too as a correction; a newer question
/// supersedes the one in flight. "Hey Cane" alone arms the gate for
/// `armedMs`: the next thing said is the command, no wake word needed.
public struct WakeWordGate: Sendable {
    public enum Output: Equatable, Sendable {
        case none
        /// The wake word with nothing after it: answer "Yes?" and listen.
        case wakeOnly
        case command(String)
    }

    /// No new words for this long, no final, and the words do not make a
    /// command yet ("how far is"): act on them anyway. Only a fallback for a
    /// final that never comes; replaying the 2026-09-26 log, volatile
    /// results stalled 2.8-4.5 s mid-sentence, and 2 s fired on "how". A
    /// fragment that stops on a word no sentence ends with ("how far is",
    /// "is there a") is never acted on this way: the final will come.
    public static let stableMs: Int64 = 5_000
    /// The same for a sentence that already parses as a command. It was
    /// 700 ms, and live that cut "how far is the sprite can" to "... the
    /// sprite" (and "water bottle" to "water"): volatile results arrive in
    /// bursts with 0.7-2.8 s gaps while the wearer is still talking. If it
    /// still fires early, the final corrects it.
    public static let completeStableMs: Int64 = 2_500
    /// "Hey Cane" then silence: say "Yes?". Saying it mutes the mic, so it
    /// must not start while the wearer is only pausing (1.5 s and 2.2 s
    /// both did in the replayed log).
    public static let wakeOnlyStableMs: Int64 = 3_000
    /// Short, because while armed *any* nearby speech counts as a command.
    public static let armedMs: Int64 = 5_000
    /// "Yes?" waits this long after a bare "Hey Cane": the recognizer often
    /// finalizes the wake word as its own segment while the command is
    /// still being said, and answering at once would mute the mic over it.
    public static let wakePromptDelayMs: Int64 = 1_200
    /// A finished sentence that is not a clear command ("Hey Cane, I'm
    /// bored.") waits this long for more: the recognizer ends a sentence at
    /// a short pause, and acting at once spoke over -- and so muted the mic
    /// for -- "is there a Rubik's cube on the table?" (2026-09-27).
    /// Anything said in this window joins the same question, no wake word.
    public static let followOnMs: Int64 = 1_500

    static let greetings: Set<String> = ["hey", "hi", "okay", "ok", "hay", "yo"]
    /// What the recognizer writes for "Cane". "king" and "keen" were heard
    /// live (2026-09-26); "can" is left out on purpose -- "hey, can you..."
    /// would wake it.
    static let names: Set<String> = [
        "cane", "kane", "cain", "caine", "kain", "cayne", "kayne", "canes", "cane's", "kane's",
        "king", "keen", "kean", "keane", "kine",
        // Heard live later on 2026-09-26.
        "cade", "kade", "gane", "gain", "gaine", "kaine", "cage",
    ]

    private var pending: String?
    private var lastChangeMs: Int64 = 0
    private var armedUntilMs: Int64?
    private var wakePromptDueMs: Int64?
    /// Set after a stability emit, so the rest of that segment (including
    /// its eventual final result) is not emitted a second time.
    private var consumedSegment = false
    /// What that early emit said ("" for a bare wake word), so the final can
    /// correct it.
    private var emittedEarly: String?
    /// A finished free question waiting `followOnMs` for more words.
    private var held: String?
    private var heldDueMs: Int64?

    /// Whether a command is already complete (defaults to "it parses").
    private let isComplete: @Sendable (String) -> Bool
    /// Whether two commands ask the same thing (a final that only adds
    /// "around" to "is there a bathroom" is not worth a second question).
    private let sameRequest: @Sendable (String, String) -> Bool

    public init(
        isComplete: @escaping @Sendable (String) -> Bool = { IntentParser.parse($0) != nil },
        sameRequest: @escaping @Sendable (String, String) -> Bool = { IntentParser.route($0) == IntentParser.route($1) }
    ) {
        self.isComplete = isComplete
        self.sameRequest = sameRequest
    }

    public mutating func feed(text: String, isFinal: Bool, nowMs: Int64) -> Output {
        let words = IntentParser.normalize(text).split(separator: " ").map(String.init)
        if consumedSegment {
            guard isFinal else { return .none }
            consumedSegment = false
            let early = emittedEarly ?? ""
            emittedEarly = nil
            // The final is the recognizer's best reading of the whole
            // sentence: if it asks for something the early emit did not
            // (or the early emit was a bare "Hey Cane" and the wearer went
            // on talking), act on it.
            guard let start = Self.commandStart(words) else { return .none }
            let final = words[start...].joined(separator: " ")
            guard !final.isEmpty, final != early, early.isEmpty || !sameRequest(early, final) else { return .none }
            wakePromptDueMs = nil
            armedUntilMs = nil
            return .command(final)
        }
        if held != nil {
            if Self.commandStart(words) != nil {
                // A new "Hey Cane": the newest question wins.
                held = nil
                heldDueMs = nil
            } else if !words.isEmpty {
                let combined = ([held!] + words).joined(separator: " ")
                if isFinal {
                    held = nil
                    heldDueMs = nil
                    pending = nil
                    return .command(combined)
                }
                // Still talking: the stability rules take over from here.
                heldDueMs = nil
                if combined != pending {
                    pending = combined
                    lastChangeMs = nowMs
                }
                return .none
            } else {
                return .none
            }
        }
        let command: [String]
        if let start = Self.commandStart(words) {
            command = Array(words[start...])
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
            if !isComplete(joined) {
                held = joined
                heldDueMs = nowMs + Self.followOnMs
                return .none
            }
            return .command(joined)
        }
        if joined != pending {
            pending = joined
            lastChangeMs = nowMs
        }
        return .none
    }

    public mutating func tick(nowMs: Int64) -> Output {
        if let due = heldDueMs, let text = held, pending == nil, nowMs >= due {
            held = nil
            heldDueMs = nil
            return .command(text)
        }
        if let due = wakePromptDueMs, pending == nil, nowMs >= due {
            wakePromptDueMs = nil
            return .wakeOnly
        }
        guard let text = pending else { return .none }
        let waited = nowMs - lastChangeMs
        if text.isEmpty, waited >= Self.wakeOnlyStableMs {
            pending = nil
            consumedSegment = true
            emittedEarly = ""
            armedUntilMs = nowMs + Self.armedMs
            return .wakeOnly
        }
        let complete = !text.isEmpty && isComplete(text)
        let fragment = !complete && Self.unfinished(text)
        if !text.isEmpty, !fragment, waited >= (complete ? Self.completeStableMs : Self.stableMs) {
            pending = nil
            held = nil
            heldDueMs = nil
            consumedSegment = true
            emittedEarly = text
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
        held = nil
        heldDueMs = nil
        consumedSegment = false
        emittedEarly = nil
    }

    /// Where the command starts, or nil when the words are not addressed to
    /// the phone:
    /// - after the last "hey/okay + Cane" (any heard spelling);
    /// - or, when a sentence *opens* with a greeting and the rest is a clear
    ///   command, after the greeting -- "Hey, how far is the Sprite can?"
    ///   (name swallowed) and "Hey team, how far..." (name misheard as a
    ///   word) were both heard live. Only commands `parse` knows, never a
    ///   free question, so "hey, what's up" to a friend stays ignored.
    static func commandStart(_ words: [String]) -> Int? {
        if let wake = lastWakeIndex(words) { return wake + 2 }
        guard let first = words.first, greetings.contains(first), words.count >= 3 else { return nil }
        let afterGreeting = words.dropFirst().joined(separator: " ")
        guard let intent = IntentParser.parse(afterGreeting) else { return nil }
        // Drop the misheard name too when the command reads the same without it.
        let afterName = words.dropFirst(2).joined(separator: " ")
        return IntentParser.parse(afterName) == intent ? 2 : 1
    }

    /// Ends on a word a finished sentence does not end on.
    static func unfinished(_ text: String) -> Bool {
        guard let last = text.split(separator: " ").last else { return true }
        return text.split(separator: " ").count < 2 || danglingEnds.contains(String(last))
    }

    static let danglingEnds: Set<String> = [
        "is", "are", "the", "a", "an", "of", "to", "for", "and", "or", "how", "what", "where", "which",
        "my", "your", "this", "that", "in", "on", "at", "far", "there", "me", "i", "it's", "its",
    ]

    static func lastWakeIndex(_ words: [String]) -> Int? {
        guard words.count >= 2 else { return nil }
        for i in stride(from: words.count - 2, through: 0, by: -1)
        where greetings.contains(words[i]) && names.contains(words[i + 1]) {
            return i
        }
        return nil
    }
}
