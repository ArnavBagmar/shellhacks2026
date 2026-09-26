import CaneContracts
import CaneFeedback
import Foundation

/// What the synthesizer is handed: the words plus the two prosody knobs
/// that carry urgency (step-10 design Amendment 3).
public struct SpokenPhrase: Equatable, Sendable {
    public let text: String
    public let pitchMultiplier: Float

    public init(text: String, pitchMultiplier: Float = 1.0) {
        self.text = text
        self.pitchMultiplier = pitchMultiplier
    }

    public var wordCount: Int {
        text.split(whereSeparator: { $0 == " " }).count
    }
}

/// Announcement -> words (step-10 design Amendment 2). Direction first at
/// every urgency, so a phrase cut off by preemption has already said the
/// part that decides the action; never a steering command; never a decimal.
public enum HazardPhrasing {
    public static let maxSpokenWords = 6
    public static let urgentPitch: Float = 1.15

    /// `nil` when the announcement is silent (urgency 1). With
    /// `hapticsDegraded` a silent announcement is spoken briefly instead:
    /// a mobility aid that lost its primary channel must still warn.
    public static func phrase(for a: Announcement, hapticsDegraded: Bool = false) -> SpokenPhrase? {
        let sector = Sector(bearingDeg: a.bearingDeg)
        switch a.speech {
        case .oneWord:
            return SpokenPhrase(text: urgentWords(sector), pitchMultiplier: urgentPitch)
        case .brief:
            return SpokenPhrase(text: brief(a, sector: sector))
        case nil:
            return hapticsDegraded ? SpokenPhrase(text: brief(a, sector: sector)) : nil
        }
    }

    /// Urgency 3 over all eight sectors (H2): locative, never "Left!".
    public static func urgentWords(_ sector: Sector) -> String {
        switch sector {
        case .ahead, .aheadLeft, .aheadRight: return "Stop!"
        case .left: return "Left side!"
        case .right: return "Right side!"
        case .behindLeft, .behindRight, .behind: return "Behind you!"
        }
    }

    public static func classWord(_ cls: ObjectClass) -> String {
        switch cls {
        case .parkedCar: return "parked car"
        case .unknownObstacle: return "obstacle"
        default: return cls.rawValue
        }
    }

    /// `n < 1` is "close", never "about 0 meters" (a spoken zero reads as
    /// impact). Bands: close / nearby / far.
    public static func rangeWords(_ range: RangeHint) -> String? {
        switch range {
        case .aboutMetres(let n) where n < 1: return "close"
        case .aboutMetres(1): return "about 1 meter"
        case .aboutMetres(let n): return "about \(n) meters"
        case .band(.near): return "close"
        case .band(.mid): return "nearby"
        case .band(.far): return "far"
        case .unknown: return nil
        }
    }

    /// `"<sector>, <class>[ fast][, <range>]"`, capped at six words by
    /// dropping range, then "fast". Sector and class are never dropped.
    /// Range is spoken only for a static class straight ahead -- the one
    /// case where distance changes what the wearer does.
    static func brief(_ a: Announcement, sector: Sector) -> String {
        let head = "\(capitalized(sector.words)), \(classWord(a.cls))"
        let fast = (a.closingMps ?? 0) > UrgencyPolicy.fastClosingMps ? " fast" : ""
        var range = ""
        if sector == .ahead, UrgencyPolicy.isStatic(a.cls), let words = rangeWords(a.range) {
            range = ", \(words)"
        }
        // "Behind you to the left, parked car" is seven words: the last
        // resort shortens the class, never the §C sector words.
        let shortHead = "\(capitalized(sector.words)), \(a.cls == .parkedCar ? "car" : classWord(a.cls))"
        for candidate in [head + fast + range, head + fast, head, shortHead] where words(candidate) <= maxSpokenWords {
            return candidate
        }
        return shortHead
    }

    private static func words(_ text: String) -> Int {
        text.split(whereSeparator: { $0 == " " }).count
    }

    static func capitalized(_ text: String) -> String {
        text.prefix(1).uppercased() + text.dropFirst()
    }
}
