import Foundation
import CaneFeedback

/// One element of a pattern. `.transient` is a tap, `.continuous` a buzz.
/// Kept as plain data so the whole alphabet is testable without CoreHaptics
/// and without a device.
public enum HapticElementKind: Equatable, Sendable {
    case transient
    case continuous(durationMs: Int)

    /// A tap has no duration of its own; CoreHaptics renders it as an
    /// impulse. 20 ms is the nominal figure the Amendment 1 `totalMs` column
    /// was computed against (`.left` = tap @200 + 20 = 220).
    public static let transientNominalMs = 20

    public var durationMs: Int {
        switch self {
        case .transient: return Self.transientNominalMs
        case .continuous(let ms): return ms
        }
    }
}

public struct HapticElement: Equatable, Sendable {
    public let kind: HapticElementKind
    public let onsetMs: Int
    /// Before gain. `HapticScript.intensity(base:level:userGain:)` applies
    /// urgency and user gain at play time.
    public let baseIntensity: Double
    public let sharpness: Double
    /// CoreHaptics' **normalized 0-1 envelope parameters, not seconds.**
    /// Verify empirically on device; do not assume units.
    public let attack: Double
    public let release: Double

    public init(kind: HapticElementKind, onsetMs: Int, baseIntensity: Double = 1.0,
                sharpness: Double, attack: Double = 0.0, release: Double = 0.0) {
        self.kind = kind
        self.onsetMs = onsetMs
        self.baseIntensity = baseIntensity
        self.sharpness = sharpness
        self.attack = attack
        self.release = release
    }

    public var endMs: Int { onsetMs + kind.durationMs }
}

/// Amendment 1 -- the alphabet has exactly five symbols.
///
/// Nine messages on one actuator is past the single-site discrimination
/// limit; five is not. Two texture families, so the first decision the body
/// makes is the robust one: discrete taps encode the lateral axis, smooth
/// buzzes encode the front/back axis.
///
/// The user's whole mental model is one sentence: *clicks are sideways --
/// two clicks wide apart is left, three quick clicks is right; smooth is
/// front or back -- a short bump is in front, a long swell is behind; a long
/// hard rattle that doesn't stop means act now.*
///
/// **Step 24 adds a sixth case, `steerTick`, which is not a sixth message.**
/// Amendment 1's five-symbol limit is about messages the wearer has to
/// *decode*; the steering tick is a continuous state signal whose information
/// is entirely in its repetition rate and its stereo side, like a reversing
/// sensor. It is never selected by `symbol(level:sector:)` and so can never
/// stand in for a hazard, and `HapticAlphabet.validate()` covers it, which is
/// how its distinctness from the five letters is proven rather than asserted.
public enum HapticSymbol: String, CaseIterable, Equatable, Sendable {
    case ahead, left, right, behind, alarm
    /// Step 24 (`docs/shepherd-steering-plan.md`, Shepherd's stage 8): one
    /// pulse of the continuous steering cue.
    case steerTick

    /// Amendment 1's **coarse 4-way reduction**, deliberately distinct from
    /// the five-band §C speech vocabulary: the haptic answers "which way do
    /// I move", and "ahead to the left" and "to your left" imply the same
    /// movement, while "behind you to the left" means "hold your line",
    /// which is the `.behind` message.
    ///
    /// `|b| < 15` -> `.ahead`; `15 <= |b| < 120` -> `.left`/`.right` by sign;
    /// `|b| >= 120` -> `.behind`.
    public static func directional(for sector: Sector) -> HapticSymbol {
        switch sector {
        case .ahead:
            return .ahead
        case .aheadQuarter(let side), .lateral(let side):
            return side == .left ? .left : .right
        case .behindQuarter, .behind:
            return .behind
        }
    }

    /// Urgency 3 always uses `.alarm` and never carries direction -- the
    /// spoken word carries it. Urgency 1 and 2 use the directional symbols
    /// and differ **only** in gain.
    public static func symbol(level: AnnouncementLevel, sector: Sector) -> HapticSymbol {
        level == .immediate ? .alarm : directional(for: sector)
    }
}

public struct HapticScript: Equatable, Sendable {
    public let symbol: HapticSymbol
    public let elements: [HapticElement]

    public var totalMs: Int { elements.map(\.endMs).max() ?? 0 }

    /// Onsets of the tap elements only. The `.alarm` bed starts at the same
    /// instant as its first tap by design, so the >= 60 ms fusion rule is a
    /// statement about consecutive *taps*, not about a buzz and a tap that
    /// are deliberately simultaneous.
    public var transientOnsets: [Int] {
        elements.filter { $0.kind == .transient }.map(\.onsetMs).sorted()
    }
}

public enum HapticAlphabet {
    // MARK: - Gain (Amendment 1: "gain, not letters")

    public static let intensityFloor = 0.35
    public static let intensityCeiling = 1.0
    public static let userGainRange = 0.6...1.4
    public static let defaultUserGain = 1.0

    /// `u1 = 0.60`, `u2 = 0.85`. `.alarm` is pinned at 1.00 and ignores both
    /// gains, so the urgency-3 cue cannot be turned down.
    public static func urgencyGain(_ level: AnnouncementLevel) -> Double {
        switch level {
        case .aware: return 0.60
        case .actSoon: return 0.85
        case .immediate: return 1.00
        }
    }

    /// The 0.35 floor exists because an unfelt urgency-1 haptic is worse
    /// than none: step 9 writes the `TrackRecord` at presentation *start*,
    /// so `refractory(1) = 8000` then suppresses that object for eight
    /// seconds on the strength of a cue the user never received.
    public static func intensity(base: Double, level: AnnouncementLevel,
                                 userGain: Double = defaultUserGain) -> Double {
        if level == .immediate { return intensityCeiling }
        let scaled = base * urgencyGain(level) * userGain
        return min(max(scaled, intensityFloor), intensityCeiling)
    }

    // MARK: - The pattern table (Amendment 1)

    public static func script(_ symbol: HapticSymbol) -> HapticScript {
        switch symbol {
        case .ahead:
            return HapticScript(symbol: .ahead, elements: [
                HapticElement(kind: .continuous(durationMs: 180), onsetMs: 0,
                              sharpness: 0.35, attack: 0.00, release: 0.10),
            ])

        // `.left` and `.right` deliberately span the same 220 ms and share
        // their first and last element: the only difference is the presence
        // of a middle click. Detecting one extra element is a more robust
        // judgment than comparing two durations, and it is not a temporal
        // mirror, so a missed or phantom first element cannot turn one side
        // into the other.
        case .left:
            return HapticScript(symbol: .left, elements: [
                HapticElement(kind: .transient, onsetMs: 0, sharpness: 0.55),
                HapticElement(kind: .transient, onsetMs: 200, sharpness: 0.55),
            ])
        case .right:
            return HapticScript(symbol: .right, elements: [
                HapticElement(kind: .transient, onsetMs: 0, sharpness: 0.55),
                HapticElement(kind: .transient, onsetMs: 100, sharpness: 0.55),
                HapticElement(kind: .transient, onsetMs: 200, sharpness: 0.55),
            ])

        // Low-frequency, slowly-rising energy is what survives cloth and
        // couples into the sternum, and `behind` is the sector the user has
        // no other channel for, so it gets the most transmissible texture.
        case .behind:
            return HapticScript(symbol: .behind, elements: [
                HapticElement(kind: .continuous(durationMs: 320), onsetMs: 0,
                              sharpness: 0.15, attack: 0.35, release: 0.20),
            ])

        // "If it is still going, it is urgent." The user never counts or
        // compares anything to recognise urgency 3 -- they notice it has not
        // stopped, the single most reliable tactile judgment available, and
        // self-reinforcing: the longer it runs the more certain they are.
        case .alarm:
            let bed = HapticElement(kind: .continuous(durationMs: 900), onsetMs: 0,
                                    sharpness: 0.30, attack: 0.00, release: 0.00)
            let taps = stride(from: 0, through: 800, by: 100).map {
                HapticElement(kind: .transient, onsetMs: $0, sharpness: 1.00)
            }
            return HapticScript(symbol: .alarm, elements: [bed] + taps)

        // Step 24. Exactly one element, so it cannot be counted against
        // `.left` (two clicks) or `.right` (three); a dull thud at sharpness
        // 0.20 rather than the 0.55 click the lateral letters use, so it does
        // not feel like a hazard even once; and the shortest thing in the
        // alphabet, so it is never occupying the actuator when a warning
        // arrives. What it means is carried by how often it repeats
        // (`SteerPolicy.pulseIntervalMs`) and which side it is panned to.
        case .steerTick:
            return HapticScript(symbol: .steerTick, elements: [
                HapticElement(kind: .transient, onsetMs: 0, sharpness: 0.20),
            ])
        }
    }

    public static var all: [HapticScript] { HapticSymbol.allCases.map(script) }

    // MARK: - Amendment 1's unit-tested invariants

    public enum ValidationFailure: Equatable, Error {
        case alarmDurationChanged(Int)
        case directionalTooLong(HapticSymbol, Int)
        case continuousElementTooLong(HapticSymbol, Int)
        case tooManyElements(HapticSymbol, Int)
        case onsetsFuse(HapticSymbol, Int)
        case duplicateScripts(HapticSymbol, HapticSymbol)
        case intensityAboveDirectionalCap(HapticSymbol, Double)
    }

    public static let maxDirectionalMs = 420
    public static let maxDirectionalContinuousMs = 400
    /// Below ~50 ms two torso pulses fuse into one.
    public static let minInterOnsetMs = 60
    public static let alarmMs = 900

    /// Asserts every Amendment 1 invariant over the whole alphabet. The test
    /// suite enumerates `HapticSymbol x urgency` and runs this.
    public static func validate(userGain: Double = defaultUserGain) -> [ValidationFailure] {
        var failures: [ValidationFailure] = []
        let scripts = all

        for script in scripts {
            let isAlarm = script.symbol == .alarm

            if isAlarm, script.totalMs != alarmMs {
                failures.append(.alarmDurationChanged(script.totalMs))
            }
            if !isAlarm, script.totalMs > maxDirectionalMs {
                failures.append(.directionalTooLong(script.symbol, script.totalMs))
            }
            if !isAlarm {
                for element in script.elements {
                    if case .continuous(let ms) = element.kind, ms > maxDirectionalContinuousMs {
                        failures.append(.continuousElementTooLong(script.symbol, ms))
                    }
                    // Every non-`.alarm` element stays at or below the
                    // urgency-2 ceiling, so nothing directional can ever be
                    // as loud as the alarm.
                    let level = AnnouncementLevel.actSoon
                    let played = intensity(base: element.baseIntensity, level: level, userGain: userGain)
                    if played > urgencyGain(level) * userGain + 1e-9 {
                        failures.append(.intensityAboveDirectionalCap(script.symbol, played))
                    }
                }
            }
            // `.alarm` is the only script containing more than three elements.
            if !isAlarm, script.elements.count > 3 {
                failures.append(.tooManyElements(script.symbol, script.elements.count))
            }
            let onsets = script.transientOnsets
            for (earlier, later) in zip(onsets, onsets.dropFirst()) where later - earlier < minInterOnsetMs {
                failures.append(.onsetsFuse(script.symbol, later - earlier))
            }
        }

        // No two scripts in the alphabet are equal.
        for (index, script) in scripts.enumerated() {
            for other in scripts[(index + 1)...] where script.elements == other.elements {
                failures.append(.duplicateScripts(script.symbol, other.symbol))
            }
        }
        return failures
    }
}
