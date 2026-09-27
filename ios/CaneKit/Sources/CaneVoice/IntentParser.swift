import Foundation

/// The fixed MVP intent set (runbook step 11) plus step 20's "find".
public enum VoiceIntent: Equatable, Sendable {
    /// "Is there a bathroom around?" -> `.find("bathroom")`.
    case find(String)
    /// "How far is the Sprite can?" -> `.howFar("sprite can")`: a find
    /// whose answer must carry a distance (TrueDepth, front camera only).
    case howFar(String)
    /// "What's around me?"
    case describeAround
    /// "What's in front of me?"
    case describeFront
    /// "Read that sign."
    case readSign
    /// "Take me to the library."
    case navigate(String)
    /// "Never mind" / "stop" / "cancel": stops whatever is going on,
    /// navigation included.
    case cancel
    /// "Stop navigation" / "cancel the route" / "end directions".
    case stopNavigation
    /// "Repeat that" / "say that again": the last direction.
    case repeatDirection
    /// "Where am I going?" / "are we there yet?".
    case navigationStatus
    /// "What time is it?" -- answered on the phone.
    case time
    /// "What can you do?" -- the command list, spoken.
    case help
    /// Anything else ("how's the weather?"): Gemini answers it from the
    /// cameras. `route` produces it; `parse` never does.
    case ask(String)
}

/// Keyword and grammar matching, no LLM: an LLM adds 0.5-2 s for a fixed
/// intent set and buys nothing (build-plan F6). Input is the command text
/// *after* the wake word.
public enum IntentParser {
    /// Phrases are matched anywhere in the sentence, not only at its start:
    /// people say "can you tell me if there's a bathroom around", and live
    /// testing (2026-09-26) answered "Sorry, I didn't catch that" to exactly
    /// that kind of sentence.
    /// `parse`, else the whole sentence as a question for Gemini -- unless
    /// it is a single stray word, which is a mishearing, not a question.
    public static func route(_ command: String) -> VoiceIntent? {
        if let intent = parse(command) { return intent }
        let text = normalize(command)
        return text.split(separator: " ").count >= 2 ? .ask(text) : nil
    }

    public static func parse(_ command: String) -> VoiceIntent? {
        let text = stripFiller(normalize(command))
        guard !text.isEmpty else { return nil }

        if cancelPhrases.contains(text) { return .cancel }
        // Before "take me to" and the finds: "stop taking me to the library"
        // must stop, not start, a route (2026-09-26: stuck rerouting, and
        // nothing the wearer said could end it).
        if stopNavigationPhrases.contains(where: text.contains) { return .stopNavigation }
        let bare = text.hasSuffix(" please") ? String(text.dropLast(" please".count)) : text
        if repeatPhrases.contains(bare) { return .repeatDirection }
        if navigationStatusPhrases.contains(where: text.contains) { return .navigationStatus }
        if timePhrases.contains(where: text.contains) { return .time }
        if text == "help" || helpPhrases.contains(where: text.contains) { return .help }
        if let place = after(navigatePrefixes, in: text).flatMap(clean) { return .navigate(place) }
        if let target = after(howFarPrefixes, in: text).flatMap(clean) { return .howFar(target) }
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

    /// Step 26 wired the gap ring into the front description, so "which way is clear"
    /// is answerable from the same bearing the steering cue is rendered from. It
    /// parsed to nil before that, and the wearer heard "Sorry, I didn't catch that" --
    /// the one question this system is now best placed to answer.
    static let frontPhrases = [
        "in front of me", "ahead of me", "what is ahead", "what do you see", "what can you see",
        "which way is clear", "which way is clearer", "which way should i go",
        "the way clear", "clear ahead", "where can i walk",
    ]
    static let aroundPhrases = ["around me", "look around", "describe", "surroundings", "what is here", "where am i"]
    static let timePhrases = ["what time", "the time", "time is it"]
    static let helpPhrases = ["what can you do", "what can i say", "what can i ask", "what do you do", "help me out"]
    static let genericTargets = ["anything", "something", "stuff", "things"]

    static let cancelPhrases: Set<String> = [
        "stop", "cancel", "never mind", "nevermind", "forget it", "be quiet", "quiet", "stop talking",
    ]

    static let howFarPrefixes = [
        "how far away is the ", "how far away is ", "how far is the ", "how far is ",
        "how far am i from the ", "how far am i from ", "how close is the ", "how close is ",
        "how close am i to the ", "how close am i to ", "distance to the ", "distance to ",
        "how far away are the ", "how far are the ", "how far are ",
        // "can you tell me how far the bench is" (filler already stripped).
        "how far away the ", "how far the ",
    ]

    static let stopNavigationPhrases = [
        "stop navigation", "stop navigating", "stop the navigation", "stop routing", "stop the route",
        "stop route", "stop directions", "stop the directions", "stop giving directions", "stop guiding",
        "stop guidance", "stop taking me", "cancel navigation", "cancel the navigation", "cancel route",
        "cancel the route", "cancel routing", "cancel directions", "cancel the directions", "end navigation",
        "end the navigation", "end route", "end the route", "end directions", "exit navigation",
        "quit navigation", "turn off navigation", "i don't want to go there", "i do not want to go there",
    ]
    /// Whole-sentence (or sentence-start) matches only: "what was that
    /// sign" is a question for Gemini, not a repeat.
    static let repeatPhrases: Set<String> = [
        "repeat", "repeat that", "repeat it", "say that again", "say it again", "repeat the direction",
        "repeat the directions", "repeat directions", "repeat the last direction", "what was that", "come again",
    ]
    static let navigationStatusPhrases = [
        "where am i going", "where are we going", "where are you taking me", "what is my destination",
        "what's my destination", "are we there yet", "how much further", "how much farther", "navigation status",
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
        // "do you see a bathroom nearby" ("can you " is stripped as filler,
        // leaving "see the door").
        "do you see any ", "do you see an ", "do you see a ", "do you see ", "can you see ",
        "see any ", "see an ", "see a ", "see the ",
        "i need to find ", "i need ", "i am looking for ", "help me find ",
    ]

    static let trailingNoise = [
        "away from me", "from me", "from here", "away", "in front of me", "ahead of me", "ahead", "on this floor", "in here", "in this building",
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
        // "how far is the" mid-sentence: a determiner is not a thing yet.
        if text.isEmpty || danglingWords.contains(text) { return nil }
        return String(text.prefix(60))
    }

    static let danglingWords: Set<String> = [
        "the", "a", "an", "some", "any", "this", "that", "these", "those", "my", "your", "our", "their",
    ]

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
        ("that's", "that is"), ("whats", "what is"), ("wheres", "where is"), ("how's", "how is"),
    ]
}
