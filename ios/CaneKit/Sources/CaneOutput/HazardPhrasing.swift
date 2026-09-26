import CaneContracts
import CaneFeedback
import Foundation

/// What the synthesizer is handed: the words plus the prosody knob that
/// carries urgency (`SpeechSettings.pitchMultiplier`).
public struct SpokenPhrase: Equatable, Sendable {
    public let text: String
    public let pitchMultiplier: Float

    public init(text: String, pitchMultiplier: Float = 1.0) {
        self.text = text
        self.pitchMultiplier = pitchMultiplier
    }
}

/// `SpeechPhrase` (the words, Amendment 2) + `SpeechSettings` (the pitch,
/// Amendment 3) -> one `SpokenPhrase` for the renderer.
public enum HazardPhrasing {
    /// `nil` when the announcement is silent (urgency 1). With
    /// `hapticsDegraded` a silent announcement is spoken in the urgency-2
    /// form instead: a mobility aid that lost its primary channel must still
    /// warn (Amendment 1, "fail audibly").
    public static func phrase(for a: Announcement, hapticsDegraded: Bool = false) -> SpokenPhrase? {
        let level = AnnouncementLevel(rawValue: a.urgency) ?? .aware
        let pitch = SpeechSettings.pitchMultiplier(level)
        if let words = SpeechPhrase.words(for: a) {
            return SpokenPhrase(text: capitalized(words), pitchMultiplier: pitch)
        }
        guard hapticsDegraded else { return nil }
        let words = SpeechPhrase.actSoonWords(
            bearingDeg: a.bearingDeg, cls: a.cls, range: a.range, closingMps: a.closingMps
        )
        return SpokenPhrase(text: capitalized(words), pitchMultiplier: pitch)
    }

    static func capitalized(_ text: String) -> String {
        text.prefix(1).uppercased() + text.dropFirst()
    }
}
