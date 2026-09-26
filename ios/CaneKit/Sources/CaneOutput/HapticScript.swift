import Foundation

/// The five-symbol haptic alphabet (step-10 design Amendment 1). The user's
/// whole model is one sentence: "Clicks are sideways: two clicks wide apart
/// is left, three quick clicks is right. Smooth is front or back: a short
/// bump is in front, a long swell is behind. A long hard rattle that doesn't
/// stop means act now."
public enum HapticSymbol: String, Equatable, Sendable, CaseIterable {
    case ahead, left, right, behind, alarm

    /// Coarse 4-way reduction of the speech sectors: the haptic answers
    /// "which way do I move", and "ahead to the left" and "to your left"
    /// imply the same movement.
    public init(sector: Sector) {
        switch sector {
        case .ahead: self = .ahead
        case .aheadLeft, .left: self = .left
        case .aheadRight, .right: self = .right
        case .behindLeft, .behindRight, .behind: self = .behind
        }
    }
}

public struct HapticElement: Equatable, Sendable {
    public enum Kind: Equatable, Sendable {
        case tap
        case buzz(durationMs: Int)
    }

    public let onsetMs: Int
    public let kind: Kind
    public let intensity: Float
    public let sharpness: Float
    /// CoreHaptics' normalised 0-1 envelope parameters, not seconds.
    public let attack: Float
    public let release: Float

    public var endMs: Int {
        switch kind {
        case .tap: return onsetMs + HapticScript.tapMs
        case .buzz(let duration): return onsetMs + duration
        }
    }
}

/// A value: a list of timed events. `CoreHapticsRenderer` turns it into a
/// `CHHapticPattern`; nothing here imports CoreHaptics, so the whole
/// vocabulary is unit-tested on macOS.
public struct HapticScript: Equatable, Sendable {
    public static let tapMs = 20
    public static let alarmMs = 900
    public static let maxNonAlarmMs = 420
    public static let maxNonAlarmBuzzMs = 400
    public static let minGapMs = 60
    public static let intensityFloor: Float = 0.35
    public static let urgencyGain: [Int: Float] = [1: 0.60, 2: 0.85]
    public static let userGainRange: ClosedRange<Float> = 0.6...1.4
    /// Navigation cues use the hazard side shapes one gain tier below
    /// urgency 1 (H-12a's "distinct gain tier"): softer, and always spoken.
    public static let navigationGain: Float = 0.45

    public let symbol: HapticSymbol?
    public let elements: [HapticElement]

    public var totalMs: Int { elements.map(\.endMs).max() ?? 0 }

    /// Urgency 3 is always `.alarm` at full intensity, ignoring every gain.
    public static func hazard(symbol: HapticSymbol, urgency: Int, userGain: Float = 1.0) -> HapticScript {
        if urgency >= 3 || symbol == .alarm { return alarm() }
        let gain = (urgencyGain[urgency] ?? urgencyGain[1]!) * clampUserGain(userGain)
        return shape(symbol, gain: gain)
    }

    public static func navigation(side: Side?, userGain: Float = 1.0) -> HapticScript? {
        guard let side else { return nil }
        let symbol: HapticSymbol = side == .left ? .left : side == .right ? .right : .ahead
        return shape(symbol, gain: navigationGain * clampUserGain(userGain))
    }

    public static func alarm() -> HapticScript {
        var elements = [element(0, .buzz(durationMs: alarmMs), 1.0, sharpness: 0.30)]
        for onset in stride(from: 0, through: 800, by: 100) {
            elements.append(element(onset, .tap, 1.0, sharpness: 1.0))
        }
        return HapticScript(symbol: .alarm, elements: elements)
    }

    private static func shape(_ symbol: HapticSymbol, gain: Float) -> HapticScript {
        let intensity = min(max(gain, intensityFloor), 1.0)
        let elements: [HapticElement]
        switch symbol {
        case .ahead:
            elements = [element(0, .buzz(durationMs: 180), intensity, sharpness: 0.35, release: 0.10)]
        case .left:
            elements = [0, 200].map { element($0, .tap, intensity, sharpness: 0.55) }
        case .right:
            elements = [0, 100, 200].map { element($0, .tap, intensity, sharpness: 0.55) }
        case .behind:
            elements = [element(0, .buzz(durationMs: 320), intensity, sharpness: 0.15, attack: 0.35, release: 0.20)]
        case .alarm:
            return alarm()
        }
        return HapticScript(symbol: symbol, elements: elements)
    }

    private static func element(
        _ onset: Int, _ kind: HapticElement.Kind, _ intensity: Float,
        sharpness: Float, attack: Float = 0, release: Float = 0
    ) -> HapticElement {
        HapticElement(onsetMs: onset, kind: kind, intensity: intensity, sharpness: sharpness,
                      attack: attack, release: release)
    }

    static func clampUserGain(_ gain: Float) -> Float {
        min(max(gain, userGainRange.lowerBound), userGainRange.upperBound)
    }

    /// The invariants that make urgency 3 unmistakable ("if it is still
    /// going, it is urgent"). Returns the first violation, `nil` if clean.
    public func violation(userGain: Float = 1.0) -> String? {
        if symbol == .alarm {
            return totalMs == Self.alarmMs ? nil : "alarm must last \(Self.alarmMs) ms"
        }
        if totalMs > Self.maxNonAlarmMs { return "longer than \(Self.maxNonAlarmMs) ms" }
        if elements.count > 3 { return "more than three elements" }
        let cap = 0.85 * Self.clampUserGain(userGain) + 0.0001
        for element in elements {
            if case .buzz(let d) = element.kind, d > Self.maxNonAlarmBuzzMs { return "buzz over 400 ms" }
            if element.intensity > max(cap, Self.intensityFloor) { return "intensity above 0.85 x gain" }
        }
        let onsets = elements.map(\.onsetMs).sorted()
        for (a, b) in zip(onsets, onsets.dropFirst()) where b - a < Self.minGapMs {
            return "inter-onset gap under \(Self.minGapMs) ms"
        }
        return nil
    }
}
