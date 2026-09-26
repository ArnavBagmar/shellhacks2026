import XCTest
import CaneFeedback
import CaneOutput

final class HapticScriptTests: XCTestCase {

    /// Amendment 1's invariants, enumerated over the whole alphabet.
    func test_alphabetSatisfiesEveryInvariant() {
        XCTAssertEqual(HapticAlphabet.validate(), [])
        for gain in [0.6, 1.0, 1.4] {
            XCTAssertEqual(HapticAlphabet.validate(userGain: gain), [], "userGain \(gain)")
        }
    }

    func test_alarmIsTheOnlyLongScript() {
        XCTAssertEqual(HapticAlphabet.script(.alarm).totalMs, 900)
        for symbol in HapticSymbol.allCases where symbol != .alarm {
            XCTAssertLessThanOrEqual(HapticAlphabet.script(symbol).totalMs, 420, "\(symbol)")
        }
    }

    /// The table in Amendment 1, asserted literally so a future edit to the
    /// pattern is a deliberate act with a failing test attached.
    func test_patternTableMatchesTheSpec() {
        XCTAssertEqual(HapticAlphabet.script(.ahead).totalMs, 180)
        XCTAssertEqual(HapticAlphabet.script(.left).totalMs, 220)
        XCTAssertEqual(HapticAlphabet.script(.right).totalMs, 220)
        XCTAssertEqual(HapticAlphabet.script(.behind).totalMs, 320)
        XCTAssertEqual(HapticAlphabet.script(.left).transientOnsets, [0, 200])
        XCTAssertEqual(HapticAlphabet.script(.right).transientOnsets, [0, 100, 200])
        XCTAssertEqual(HapticAlphabet.script(.alarm).transientOnsets,
                       [0, 100, 200, 300, 400, 500, 600, 700, 800])
    }

    /// Left and right span the same duration and share their first and last
    /// element: the difference is one extra click, not a temporal mirror, so
    /// a missed or phantom element cannot turn one side into the other.
    func test_leftAndRightDifferOnlyByTheMiddleClick() {
        let left = HapticAlphabet.script(.left)
        let right = HapticAlphabet.script(.right)
        XCTAssertEqual(left.totalMs, right.totalMs)
        XCTAssertEqual(left.transientOnsets.first, right.transientOnsets.first)
        XCTAssertEqual(left.transientOnsets.last, right.transientOnsets.last)
        XCTAssertEqual(right.transientOnsets.count - left.transientOnsets.count, 1)
    }

    /// The haptic reduction is coarser than the speech vocabulary on
    /// purpose: it answers "which way do I move".
    func test_sectorReducesToFourDirectionalSymbols() {
        XCTAssertEqual(HapticSymbol.directional(for: BearingSector.sector(0)), .ahead)
        XCTAssertEqual(HapticSymbol.directional(for: BearingSector.sector(14.9)), .ahead)
        // "ahead to the left" and "to your left" imply the same movement.
        XCTAssertEqual(HapticSymbol.directional(for: BearingSector.sector(-15)), .left)
        XCTAssertEqual(HapticSymbol.directional(for: BearingSector.sector(-90)), .left)
        XCTAssertEqual(HapticSymbol.directional(for: BearingSector.sector(90)), .right)
        // "behind you to the left" means "hold your line" -- the behind message.
        XCTAssertEqual(HapticSymbol.directional(for: BearingSector.sector(-120)), .behind)
        XCTAssertEqual(HapticSymbol.directional(for: BearingSector.sector(180)), .behind)
    }

    func test_urgencyThreeIsAlwaysTheAlarmAndNeverDirectional() {
        for bearing in stride(from: -180.0, through: 180.0, by: 5) {
            let sector = BearingSector.sector(bearing)
            XCTAssertEqual(HapticSymbol.symbol(level: .immediate, sector: sector), .alarm)
        }
    }

    /// Urgency 1 and 2 use the same letters and differ only in gain.
    func test_urgencyOneAndTwoShareLettersAndDifferOnlyInGain() {
        let sector = BearingSector.sector(-90)
        XCTAssertEqual(HapticSymbol.symbol(level: .aware, sector: sector),
                       HapticSymbol.symbol(level: .actSoon, sector: sector))
        let aware = HapticAlphabet.intensity(base: 1.0, level: .aware)
        let actSoon = HapticAlphabet.intensity(base: 1.0, level: .actSoon)
        XCTAssertLessThan(aware, actSoon)
        XCTAssertEqual(aware, 0.60, accuracy: 1e-9)
        XCTAssertEqual(actSoon, 0.85, accuracy: 1e-9)
    }

    /// An unfelt urgency-1 haptic is worse than none: step 9 has already
    /// recorded that the user was warned.
    func test_intensityNeverFallsBelowTheFloor() {
        for gain in stride(from: 0.6, through: 1.4, by: 0.1) {
            let value = HapticAlphabet.intensity(base: 1.0, level: .aware, userGain: gain)
            XCTAssertGreaterThanOrEqual(value, HapticAlphabet.intensityFloor)
            XCTAssertLessThanOrEqual(value, HapticAlphabet.intensityCeiling)
        }
    }

    /// The alarm cannot be turned down.
    func test_alarmIgnoresBothGains() {
        for gain in [0.6, 1.0, 1.4] {
            XCTAssertEqual(HapticAlphabet.intensity(base: 1.0, level: .immediate, userGain: gain), 1.0)
        }
    }

    func test_noTwoSymbolsProduceTheSamePattern() {
        let scripts = HapticAlphabet.all
        for (index, script) in scripts.enumerated() {
            for other in scripts[(index + 1)...] {
                XCTAssertNotEqual(script.elements, other.elements, "\(script.symbol) == \(other.symbol)")
            }
        }
    }

    /// Below ~50 ms two torso pulses fuse into one.
    func test_tapsNeverFuse() {
        for script in HapticAlphabet.all {
            let onsets = script.transientOnsets
            for (earlier, later) in zip(onsets, onsets.dropFirst()) {
                XCTAssertGreaterThanOrEqual(later - earlier, HapticAlphabet.minInterOnsetMs,
                                            "\(script.symbol)")
            }
        }
    }
}
