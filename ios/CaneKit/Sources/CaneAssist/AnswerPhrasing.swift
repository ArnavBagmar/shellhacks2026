import CaneOutput
import CaneTransport
import Foundation

/// A laptop `answer` -> words. Direction comes only from `bearingDeg`, in
/// the same §C sector words hazards use ("to your right"), and it comes
/// first -- one vocabulary for the wearer, whoever is talking.
public enum AnswerPhrasing {
    public static let holdStill = "Hold still, looking around."
    public static let lookingAhead = "Hold still, looking ahead."
    /// Before a free question: it may or may not need the cameras, so no
    /// "hold still".
    public static let checking = "Let me check."
    public static let noLink = "I can't reach the laptop right now."
    public static let timedOut = "Sorry, that took too long."
    public static let cancelled = "Okay."
    public static let notUnderstood = "Sorry, I didn't catch that."
    public static let help = "Say Hey Cane, then ask me anything, like: is there a bathroom around, "
        + "how far is that chair, what's in front of me, or take me to a place. "
        + "While navigating: repeat that, where am I going, or stop navigation."
    public static let listening = "Yes?"

    /// `distance` is what the TrueDepth map said about the answer's box;
    /// `asked` is true for "how far is ...", which always gets a word on
    /// distance -- a number, or why there is none. Unasked, only a measured
    /// distance is added.
    public static func phrase(
        for answer: SceneAnswer, target: String?, distance: DistanceOutcome? = nil, asked: Bool = false,
        locale: Locale = .current
    ) -> String {
        switch answer.status {
        case .found:
            let subject = answer.subject ?? target ?? "it"
            let maybe = answer.confidence == .low ? "Maybe " : ""
            let detail = answer.detail.map { ", " + clause($0) } ?? ""
            let (howFar, after) = distanceWords(distance, asked: asked, locale: locale)
            guard let bearing = answer.bearingDeg else {
                return capitalized("\(maybe)\(subject) nearby\(howFar), direction unclear\(detail).") + after
            }
            let sector = BearingSector.phrase(bearing)
            let lead = maybe.isEmpty ? capitalized(sector) : "\(maybe)\(sector)"
            return "\(lead), \(subject)\(howFar)\(detail)." + after
        case .notFound:
            guard let target else { return "I don't see it nearby." }
            return "I don't see \(article(for: target)) \(target) nearby."
        case .described:
            return answer.detail ?? "I can't describe it right now."
        case .unavailable:
            return "Looking around isn't available right now."
        }
    }

    /// A clause after the subject (", about 4 feet away") and a sentence
    /// after the answer (" Face it and ask again ..."), either may be empty.
    static func distanceWords(_ outcome: DistanceOutcome?, asked: Bool, locale: Locale) -> (String, String) {
        switch outcome {
        case .measured(let sample):
            return (", " + DistanceWords.away(sample.metres, locale: locale), "")
        case .tooFar where asked:
            return (", " + DistanceWords.beyondRange(locale: locale), "")
        case .outOfView where asked:
            return ("", " " + faceItToMeasure)
        case .noSensor where asked:
            return ("", " " + noDepthSensor)
        case .unreadable where asked:
            return ("", " " + noCleanReading)
        default:
            return ("", "")
        }
    }

    public static let faceItToMeasure = "Face it and ask again to measure how far."
    public static let noDepthSensor = "This phone can't measure distance."
    public static let noCleanReading = "I can't get a clean distance reading right now."

    /// "It's 3:07 PM." in the phone's own clock style.
    public static func time(_ date: Date = Date(), locale: Locale = .current, timeZone: TimeZone = .current) -> String {
        var style = Date.FormatStyle(date: .omitted, time: .shortened)
        style.locale = locale
        style.timeZone = timeZone
        return "It's \(date.formatted(style))."
    }

    /// Words heard but no command matched: say what was heard (a mishearing
    /// and an unsupported question sound different to the wearer) and what
    /// does work, instead of a bare "didn't catch that".
    public static func notUnderstood(heard: String) -> String {
        "I heard: \(heard). I can find things, or tell you what's around."
    }

    /// The side to buzz for a found answer (navigation-tier, never a hazard
    /// symbol), `nil` when there is no bearing.
    public static func side(for answer: SceneAnswer) -> Side? {
        guard answer.status == .found, let bearing = answer.bearingDeg else { return nil }
        return BearingSector.sector(bearing).side
    }

    static func article(for noun: String) -> String {
        let lower = noun.lowercased()
        if lower.hasSuffix("s"), !lower.hasSuffix("ss") { return "any" }
        return "aeiou".contains(lower.first ?? "x") ? "an" : "a"
    }

    /// Gemini's detail as a mid-sentence clause: "On the table." -> "on the
    /// table" (it is spoken after a comma, before our own full stop).
    static func clause(_ text: String) -> String {
        var text = text.trimmingCharacters(in: .whitespaces)
        while let last = text.last, ".!;,".contains(last) { text.removeLast() }
        guard let first = text.first, first.isUppercase else { return text }
        // Keep acronyms and names ("ATM", "Starbucks"): only lower a plain
        // capitalised first word.
        let word = text.prefix { $0.isLetter }
        let restLower = word.dropFirst().allSatisfy { $0.isLowercase }
        let common = ["on", "in", "at", "by", "near", "next", "past", "behind", "under", "the", "a", "to", "about"]
        return restLower && common.contains(word.lowercased()) ? first.lowercased() + text.dropFirst() : text
    }

    static func capitalized(_ text: String) -> String {
        text.prefix(1).uppercased() + text.dropFirst()
    }
}
