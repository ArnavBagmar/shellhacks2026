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
        + "what's in front of me, read that sign, or take me to a place."
    public static let listening = "Yes?"

    public static func phrase(for answer: SceneAnswer, target: String?) -> String {
        switch answer.status {
        case .found:
            let subject = answer.subject ?? target ?? "it"
            let maybe = answer.confidence == .low ? "Maybe " : ""
            let detail = answer.detail.map { ", \($0)" } ?? ""
            guard let bearing = answer.bearingDeg else {
                return capitalized("\(maybe)\(subject) nearby, direction unclear\(detail).")
            }
            let sector = BearingSector.phrase(bearing)
            let lead = maybe.isEmpty ? capitalized(sector) : "\(maybe)\(sector)"
            return "\(lead), \(subject)\(detail)."
        case .notFound:
            guard let target else { return "I don't see it nearby." }
            return "I don't see \(article(for: target)) \(target) nearby."
        case .described:
            return answer.detail ?? "I can't describe it right now."
        case .unavailable:
            return "Looking around isn't available right now."
        }
    }

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

    static func capitalized(_ text: String) -> String {
        text.prefix(1).uppercased() + text.dropFirst()
    }
}
