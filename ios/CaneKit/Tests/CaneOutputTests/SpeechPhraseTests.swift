import XCTest
import CaneContracts
import CaneFeedback
import CaneOutput

final class SpeechPhraseTests: XCTestCase {

    // MARK: - Urgency 3 (Amendment 2's H2 table)

    /// Every entry is locative or a self-contained imperative. The system
    /// does not model free space, so it must never tell the user which way
    /// to move.
    func test_immediateWordsAreNeverSteeringCommands() {
        XCTAssertEqual(SpeechPhrase.immediateWords(sector: .ahead), "Stop!")
        XCTAssertEqual(SpeechPhrase.immediateWords(sector: .aheadQuarter(.left)), "Stop!")
        XCTAssertEqual(SpeechPhrase.immediateWords(sector: .aheadQuarter(.right)), "Stop!")
        XCTAssertEqual(SpeechPhrase.immediateWords(sector: .lateral(.left)), "Left side!")
        XCTAssertEqual(SpeechPhrase.immediateWords(sector: .lateral(.right)), "Right side!")
        XCTAssertEqual(SpeechPhrase.immediateWords(sector: .behindQuarter(.left)), "Behind you!")
        XCTAssertEqual(SpeechPhrase.immediateWords(sector: .behindQuarter(.right)), "Behind you!")
        XCTAssertEqual(SpeechPhrase.immediateWords(sector: .behind), "Behind you!")

        // "Left!" and "Right!" are indistinguishable from steering commands,
        // the one error that puts the user in the road.
        XCTAssertNotEqual(SpeechPhrase.immediateWords(sector: .lateral(.left)), "Left!")
        XCTAssertNotEqual(SpeechPhrase.immediateWords(sector: .lateral(.right)), "Right!")
    }

    /// `.oneWord` means "<= 2 words, <= 450 ms", not literally one word.
    func test_immediateWordsFitTheOneWordBudget() {
        for sector: Sector in [.ahead, .aheadQuarter(.left), .lateral(.left),
                               .lateral(.right), .behindQuarter(.right), .behind] {
            XCTAssertLessThanOrEqual(SpeechPhrase.wordCount(SpeechPhrase.immediateWords(sector: sector)), 2)
        }
    }

    // MARK: - Range words

    /// A spoken zero reads as impact.
    func test_rangeNeverSpeaksZeroOrADecimal() {
        XCTAssertEqual(SpeechPhrase.rangeWords(.aboutMetres(0)), "close")
        XCTAssertEqual(SpeechPhrase.rangeWords(.aboutMetres(-1)), "close")
        XCTAssertEqual(SpeechPhrase.rangeWords(.aboutMetres(1)), "about 1 meter")
        XCTAssertEqual(SpeechPhrase.rangeWords(.aboutMetres(4)), "about 4 meters")
        XCTAssertEqual(SpeechPhrase.rangeWords(.band(.near)), "close")
        XCTAssertEqual(SpeechPhrase.rangeWords(.band(.mid)), "nearby")
        XCTAssertEqual(SpeechPhrase.rangeWords(.band(.far)), "far")
        XCTAssertNil(SpeechPhrase.rangeWords(.unknown))
    }

    // MARK: - The urgency-2 template

    func test_directionLeadsEveryPhrase() {
        let phrase = SpeechPhrase.actSoonWords(bearingDeg: -90, cls: .cyclist,
                                               range: .band(.near), closingMps: nil)
        XCTAssertEqual(phrase, "to your left, cyclist")
        XCTAssertTrue(phrase.hasPrefix("to your left"))
    }

    /// Range is spoken only straight ahead and only for a static class --
    /// the case where distance changes what the user does.
    func test_rangeOnlyWhenAheadAndStatic() {
        XCTAssertEqual(
            SpeechPhrase.actSoonWords(bearingDeg: 5, cls: .pole, range: .aboutMetres(4), closingMps: nil),
            "straight ahead, pole, about 4 meters")
        // Moving class ahead: no range.
        XCTAssertEqual(
            SpeechPhrase.actSoonWords(bearingDeg: 5, cls: .cyclist, range: .aboutMetres(4), closingMps: nil),
            "straight ahead, cyclist")
        // Static class off to the side: no range.
        XCTAssertEqual(
            SpeechPhrase.actSoonWords(bearingDeg: 90, cls: .pole, range: .aboutMetres(4), closingMps: nil),
            "to your right, pole")
    }

    /// "closing" is never spoken -- it costs ~250 ms to say what the urgency
    /// level already said.
    func test_fastReplacesTheWordClosing() {
        let phrase = SpeechPhrase.actSoonWords(bearingDeg: 90, cls: .car,
                                               range: .unknown, closingMps: 5.0)
        XCTAssertEqual(phrase, "to your right, car fast")
        XCTAssertFalse(phrase.contains("closing"))

        // At or below the step-9 threshold, nothing is added.
        XCTAssertEqual(
            SpeechPhrase.actSoonWords(bearingDeg: 90, cls: .car, range: .unknown, closingMps: 3.0),
            "to your right, car")
    }

    /// Amendment 2's worked example: 7 words renders as 6 by dropping
    /// "fast". The sector words and the class word are never dropped.
    func test_wordBudgetDropsFastBeforeTheSectorWords() {
        let phrase = SpeechPhrase.actSoonWords(bearingDeg: -150, cls: .cyclist,
                                               range: .unknown, closingMps: 9.0)
        XCTAssertEqual(phrase, "behind you to the left, cyclist")
        XCTAssertEqual(SpeechPhrase.wordCount(phrase), 6)
        XCTAssertTrue(phrase.hasPrefix("behind you to the left"))
    }

    /// Range is dropped before "fast".
    func test_rangeIsDroppedFirst() {
        // "straight ahead, pole fast, about 4 meters" is 7 words; range goes.
        let phrase = SpeechPhrase.actSoonWords(bearingDeg: 0, cls: .pole,
                                               range: .aboutMetres(4), closingMps: 9.0)
        XCTAssertEqual(phrase, "straight ahead, pole fast")
        XCTAssertLessThanOrEqual(SpeechPhrase.wordCount(phrase), SpeechPhrase.maxSpokenWords)
    }

    /// Every class in the closed enum has a spoken form.
    func test_everyClassHasWords() {
        for cls in ObjectClass.allCases {
            XCTAssertFalse(SpeechPhrase.classWord(cls).isEmpty, "\(cls)")
        }
    }

    // MARK: - Amendment 3 timing

    /// `max(hapticMs, lead + speechMs)` -- not `max` alone, not the sum.
    func test_presentationDurationAccountsForTheSpeechLead() {
        XCTAssertEqual(SpeechSettings.presentationDurationMs(hapticMs: 900, speechMs: 400), 900)
        XCTAssertEqual(SpeechSettings.presentationDurationMs(hapticMs: 220, speechMs: 400), 520)
        XCTAssertEqual(SpeechSettings.presentationDurationMs(hapticMs: 220, speechMs: nil), 220)
        // Never the sum.
        XCTAssertNotEqual(SpeechSettings.presentationDurationMs(hapticMs: 900, speechMs: 400), 1300)
    }

    func test_urgencyThreeRaisesPitch() {
        XCTAssertEqual(SpeechSettings.pitchMultiplier(.immediate), 1.15)
        XCTAssertEqual(SpeechSettings.pitchMultiplier(.actSoon), 1.00)
        XCTAssertEqual(SpeechSettings.pitchMultiplier(.aware), 1.00)
    }

    /// A pre-delay is pure latency against a 2 s TTC; a post-delay holds the
    /// synthesizer busy and extends the cancellation budget.
    func test_utteranceDelaysAreZero() {
        XCTAssertEqual(SpeechSettings.preUtteranceDelay, 0)
        XCTAssertEqual(SpeechSettings.postUtteranceDelay, 0)
        XCTAssertTrue(SpeechSettings.rateRange.contains(SpeechSettings.defaultRate))
    }
}
