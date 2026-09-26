import CaneOutput
import CaneTransport
import Foundation

/// A laptop `answer` -> words. Direction comes only from `bearingDeg`, in
/// the same §C sector words hazards use ("to your right"), and it comes
/// first -- one vocabulary for the wearer, whoever is talking.
public enum AnswerPhrasing {
    public static let holdStill = "Hold still, looking around."
    public static let lookingAhead = "Hold still, looking ahead."
    public static let noLink = "I can't reach the laptop right now."
    public static let timedOut = "Sorry, that took too long."
    public static let cancelled = "Okay."
    public static let notUnderstood = "Sorry, I didn't catch that."
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
