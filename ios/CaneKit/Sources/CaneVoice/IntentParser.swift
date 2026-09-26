import Foundation

/// The fixed MVP intent set (runbook step 11) plus step 20's "find".
public enum VoiceIntent: Equatable, Sendable {
    /// "Is there a bathroom around?" -> `.find("bathroom")`.
    case find(String)
    /// "What's around me?"
    case describeAround
    /// "What's in front of me?"
    case describeFront
    /// "Read that sign."
    case readSign
    /// "Take me to the library."
    case navigate(String)
    /// "Never mind" / "stop" / "cancel".
    case cancel
}

/// Keyword and grammar matching, no LLM: an LLM adds 0.5-2 s for a fixed
/// intent set and buys nothing (build-plan F6). Input is the command text
/// *after* the wake word.
public enum IntentParser {
    /// Phrases are matched anywhere in the sentence, not only at its start:
    /// people say "can you tell me if there's a bathroom around", and live
    /// testing (2026-09-26) answered "Sorry, I didn't catch that" to exactly
    /// that kind of sentence.
    public static func parse(_ command: String) -> VoiceIntent? {
        let text = stripFiller(normalize(command))
        guard !text.isEmpty else { return nil }

        if cancelPhrases.contains(text) { return .cancel }
        if let place = after(navigatePrefixes, in: text).flatMap(clean) { return .navigate(place) }
        if let target = after(findPrefixes, in: text).flatMap(clean) {
            if genericTargets.contains(where: { target == $0 || target.hasPrefix($0 + " ") }) {
                return frontPhrases.contains(where: text.contains) ? .describeFront : .describeAround
            }
            return .find(target)
        }
        if frontPhrases.contains(where: text.contains) { return .describeFront }
        if aroundPhrases.contains(where: text.contains) || text == "where am i" { return .describeAround }
        if text == "read" || (" " + text).contains(" read ") || text.contains("sign say") || text.contains("does that say") {
            return .readSign
        }
        return nil
    }

    /// The text after the earliest (then longest) marker found as whole words.
    static func after(_ markers: [String], in text: String) -> String? {
        let padded = " " + text + " "
        var best: (start: String.Index, end: String.Index)?
        for marker in markers {
            guard let range = padded.range(of: " " + marker) else { continue }
            if best == nil || range.lowerBound < best!.start
                || (range.lowerBound == best!.start && range.upperBound > best!.end) {
                best = (range.lowerBound, range.upperBound)
            }
        }
        guard let best else { return nil }
        return String(padded[best.end...]).trimmingCharacters(in: .whitespaces)
    }

    static let fillers = [
        "hey ", "okay ", "ok ", "um ", "uh ", "so ", "please ", "can you ", "could you ", "would you ",
        "tell me ", "let me know ", "do you know ", "i want to know ", "i wonder ", "if ", "whether ",
    ]

    static func stripFiller(_ text: String) -> String {
        var text = text
        var changed = true
        while changed {
            changed = false
            for filler in fillers where text.hasPrefix(filler) {
                text = String(text.dropFirst(filler.count))
                changed = true
            }
        }
        return text
    }

    static let frontPhrases = ["in front of me", "ahead of me", "what is ahead", "what do you see", "what can you see"]
    static let aroundPhrases = ["around me", "look around", "describe", "surroundings", "what is here", "where am i"]
    static let genericTargets = ["anything", "something", "stuff", "things"]

    static let cancelPhrases: Set<String> = [
        "stop", "cancel", "never mind", "nevermind", "forget it", "be quiet", "quiet", "stop talking",
    ]

    static let navigatePrefixes = [
        "take me to ", "navigate to ", "directions to ", "guide me to ", "walk me to ", "get me to ",
    ]

    /// Longest first where one prefix extends another.
    static let findPrefixes = [
        "is there any ", "is there an ", "is there a ", "is there ", "are there any ", "are there ",
        "there is any ", "there is an ", "there is a ", "there are any ", "there are ",
        "where the nearest ", "where the closest ", "where the ", "where a ", "where an ",
        "where is the nearest ", "where is the closest ", "where is the ", "where is a ", "where is an ", "where is ",
        "where are the ", "where are ",
        "can you find me ", "can you find ", "find me ", "find ", "look for ", "search for ",
        "i need to find ", "i need ", "i am looking for ", "help me find ",
    ]

    static let trailingNoise = [
        "in front of me", "ahead of me", "ahead", "on this floor", "in here", "in this building",
        "around here", "around me", "near me", "close by", "nearby", "around", "near", "here", "close", "please",
        "anywhere", "for me", "located", "is", "are", "at",
    ]
    static let articles = ["a ", "an ", "the ", "some ", "any ", "nearest ", "closest "]

    /// "a bathroom around here please" -> "bathroom".
    static func clean(_ raw: String) -> String? {
        var text = raw.trimmingCharacters(in: .whitespaces)
        var changed = true
        while changed {
            changed = false
            for noise in trailingNoise where text == noise || text.hasSuffix(" " + noise) {
                text = String(text.dropLast(noise.count)).trimmingCharacters(in: .whitespaces)
                changed = true
            }
            for article in articles where text.hasPrefix(article) {
                text = String(text.dropFirst(article.count))
                changed = true
            }
        }
        return text.isEmpty ? nil : String(text.prefix(60))
    }

    /// Lowercase, straight apostrophes, no punctuation, contractions
    /// expanded, single spaces.
    public static func normalize(_ raw: String) -> String {
        var text = raw.lowercased().replacingOccurrences(of: "\u{2019}", with: "'")
        text = String(text.map { $0.isLetter || $0.isNumber || $0 == "'" ? $0 : " " })
        for (short, long) in contractions {
            text = text.replacingOccurrences(of: short, with: long)
        }
        return text.split(separator: " ").joined(separator: " ")
    }

    static let contractions = [
        ("what's", "what is"), ("where's", "where is"), ("there's", "there is"), ("i'm", "i am"),
        ("that's", "that is"), ("whats", "what is"), ("wheres", "where is"),
    ]
}
