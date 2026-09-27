import Foundation

/// The moments Cane talks while it works.
public enum PromptMoment: CaseIterable, Sendable {
    /// Before a find or "what's around me".
    case lookAround
    /// Before "what's in front of me".
    case lookAhead
    /// Before "how far is the ...".
    case measure
    /// Before any other question.
    case check
    /// Gemini is slow: one reassurance so silence is not taken for a dead link.
    case stillLooking
    /// A bare "Hey Cane".
    case listening
}

/// What Cane says in each moment. "Hold still, looking around." every single
/// time sounded robotic (2026-09-26), so each moment has a pool, and a line
/// is never repeated back to back.
///
/// Keep `COMMON_PHRASES` in `backend/src/cane/scene/voice.py` equal to
/// `PromptPhrases.all`: the laptop voices those at start-up so they play
/// instantly in the natural voice.
public enum PromptPhrases {
    public static func pool(_ moment: PromptMoment) -> [String] {
        switch moment {
        case .lookAround: [
            "Hold still, looking around.", "Hmm, let me look around.", "One sec, looking around.",
            "Okay, let me take a look.", "Hmm, give me a second.", "Let me see what's around.",
        ]
        case .lookAhead: [
            "Hold still, looking ahead.", "Hmm, let me look ahead.", "One sec, checking in front of you.",
            "Okay, let me see what's ahead.",
        ]
        case .measure: [
            "Hmm, let me measure that.", "One sec, measuring.", "Let me see how far.", "Okay, checking the distance.",
        ]
        case .check: [
            "Let me check.", "Hmm, one sec.", "Okay, let me see.", "Hmm, let me think.", "Good question, one sec.",
        ]
        case .stillLooking: [
            "Still looking.", "Hang on, almost there.", "Hmm, still checking.", "Just a moment more.",
        ]
        case .listening: [
            "Yes?", "Mm-hm?", "I'm listening.", "Go ahead.",
        ]
        }
    }

    /// Every line, for pre-voicing.
    public static var all: [String] { PromptMoment.allCases.flatMap(pool) }
}

/// Picks a line per moment, never the one it picked last time for that
/// moment. `choose(n)` returns an index in `0..<n` (random by default;
/// tests pass a fixed one).
public struct PromptPicker: Sendable {
    private var last: [PromptMoment: String] = [:]
    private let choose: @Sendable (Int) -> Int

    public init(choose: @escaping @Sendable (Int) -> Int = { Int.random(in: 0..<$0) }) {
        self.choose = choose
    }

    public mutating func next(_ moment: PromptMoment) -> String {
        var options = PromptPhrases.pool(moment)
        if options.count > 1, let previous = last[moment] {
            options.removeAll { $0 == previous }
        }
        let line = options[min(max(choose(options.count), 0), options.count - 1)]
        last[moment] = line
        return line
    }
}
