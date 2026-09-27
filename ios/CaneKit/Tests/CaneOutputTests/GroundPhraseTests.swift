import CaneFeedback
import CaneTransport
import XCTest

@testable import CaneOutput

final class GroundPhraseTests: XCTestCase {
    private func cue(bearingDeg: Double) throws -> GroundCue {
        try GroundCue(
            tCaptureMs: 1_790_000_000_000, ttlMs: 1000, bearingDeg: bearingDeg,
            spanDeg: 3.75, dropRatio: 2.4, confidence: 0.64, source: .x4Cloud
        )
    }

    func testDirectionComesFirst() throws {
        XCTAssertEqual(try GroundPhrase.words(for: cue(bearingDeg: -30)), "ahead to the left, drop")
        XCTAssertEqual(try GroundPhrase.words(for: cue(bearingDeg: 0)), "straight ahead, drop")
    }

    /// The output layer never issues a steering command, and "step down" is
    /// both an instruction and the wrong one if the hole is deep.
    func testItNeverTellsTheWearerToStep() throws {
        for bearing in stride(from: -170.0, through: 180.0, by: 10.0) {
            let words = try GroundPhrase.words(for: cue(bearingDeg: bearing))
            XCTAssertFalse(words.contains("step"), "\(words) instructs the wearer")
        }
    }

    /// The sensor cannot know a depth, so no code path may speak one.
    func testNoNumberIsEverSpoken() throws {
        for bearing in stride(from: -170.0, through: 180.0, by: 10.0) {
            let words = try GroundPhrase.words(for: cue(bearingDeg: bearing))
            XCTAssertNil(words.rangeOfCharacter(from: .decimalDigits), words)
        }
    }

    func testEveryPhraseFitsTheSpokenWordBudget() throws {
        for bearing in stride(from: -170.0, through: 180.0, by: 10.0) {
            let words = try GroundPhrase.words(for: cue(bearingDeg: bearing))
            XCTAssertLessThanOrEqual(SpeechPhrase.wordCount(words), SpeechPhrase.maxSpokenWords, words)
        }
    }

    func testThePlanIsDirectionalAndNeverTheAlarm() throws {
        let plan = GroundPhrase.plan(
            for: try cue(bearingDeg: -30), hapticsAvailable: true,
            userGain: 1.0, route: .headphones, channels: 2
        )

        XCTAssertEqual(plan.haptic?.symbol, .left)
        XCTAssertNotEqual(plan.haptic?.symbol, .alarm, "a hole is not closing on you")
        XCTAssertLessThan(plan.pan, 0)
    }

    func testWithoutHapticsThePhraseStillSpeaks() throws {
        let plan = GroundPhrase.plan(
            for: try cue(bearingDeg: -30), hapticsAvailable: false,
            userGain: 1.0, route: .headphones, channels: 2
        )

        XCTAssertNil(plan.haptic)
        XCTAssertEqual(plan.phrase?.text, "ahead to the left, drop")
        XCTAssertEqual(plan.pan, 0)
    }
}
