import CaneFeedback
import Foundation

/// From the alphabet (`HapticAlphabet`, Amendment 1) to what the renderer
/// plays. `HapticAlphabet.script` carries *base* intensities; these helpers
/// return copies whose `baseIntensity` is the intensity actually played
/// (urgency gain x user gain, the 0.35 floor, the alarm pinned at 1.0), and
/// the renderer plays that value as-is.
extension HapticScript {
    /// A hazard cue: `symbol` at `level`'s gain.
    public static func hazard(_ symbol: HapticSymbol, level: AnnouncementLevel,
                              userGain: Double = HapticAlphabet.defaultUserGain) -> HapticScript {
        let gain = clampedUserGain(userGain)
        return HapticAlphabet.script(symbol).scaled {
            HapticAlphabet.intensity(base: $0, level: symbol == .alarm ? .immediate : level, userGain: gain)
        }
    }

    /// Navigation turn cues reuse the side shapes one gain tier *below*
    /// urgency 1 (H-12a's "distinct gain tier"): softer than any hazard, and
    /// always spoken. `nil` side means no haptic (u-turn, arrival, GPS).
    public static func navigation(side: Side?, userGain: Double = HapticAlphabet.defaultUserGain) -> HapticScript? {
        guard let side else { return nil }
        let symbol: HapticSymbol = side == .left ? .left : side == .right ? .right : .ahead
        let intensity = min(max(navigationGain * clampedUserGain(userGain), HapticAlphabet.intensityFloor), 1.0)
        return HapticAlphabet.script(symbol).scaled { _ in intensity }
    }

    public static let navigationGain = 0.45

    static func clampedUserGain(_ gain: Double) -> Double {
        min(max(gain, HapticAlphabet.userGainRange.lowerBound), HapticAlphabet.userGainRange.upperBound)
    }

    func scaled(_ intensity: (Double) -> Double) -> HapticScript {
        HapticScript(symbol: symbol, elements: elements.map {
            HapticElement(kind: $0.kind, onsetMs: $0.onsetMs, baseIntensity: intensity($0.baseIntensity),
                          sharpness: $0.sharpness, attack: $0.attack, release: $0.release)
        })
    }
}
