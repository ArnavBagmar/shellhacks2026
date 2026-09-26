import CaneContracts
import CaneFeedback
import XCTest

@testable import CaneOutput

/// Timing, preemption and cancellation, with fake renderers whose durations
/// are known (step-10 design Amendment 7's automated list).
final class OutputArbiterTests: XCTestCase {
    func testHapticAndSpeechRunConcurrentlyWithTheSpeechLead() async {
        let haptics = FakeHaptics(durationMs: 300)
        let speech = FakeSpeech(durationMs: 300)
        let arbiter = OutputArbiter(haptics: haptics, speech: speech)

        let ms = await elapsedMs {
            await arbiter.presentHazard(haptic: HapticScript.hazard(symbol: .left, urgency: 2),
                                        phrase: SpokenPhrase(text: "To your left, cyclist"), pan: 0)
        }

        // max(300, 120 + 300) = 420, never the 600 ms sum.
        XCTAssertGreaterThanOrEqual(ms, 400)
        XCTAssertLessThan(ms, 560)
        XCTAssertEqual(haptics.played.count, 1)
        XCTAssertEqual(speech.spoken.map(\.text), ["To your left, cyclist"])
    }

    func testASilentAnnouncementNeverTouchesSpeech() async {
        let speech = FakeSpeech()
        let arbiter = OutputArbiter(haptics: FakeHaptics(), speech: speech)

        await arbiter.presentHazard(haptic: HapticScript.hazard(symbol: .ahead, urgency: 1), phrase: nil, pan: 0)

        XCTAssertTrue(speech.spoken.isEmpty)
        XCTAssertFalse(arbiter.isSpeaking)
    }

    func testCancelHazardReturnsWithinFiftyMilliseconds() async throws {
        let arbiter = OutputArbiter(haptics: FakeHaptics(durationMs: 60_000), speech: FakeSpeech(durationMs: 60_000))
        let presenting = Task {
            await arbiter.presentHazard(haptic: HapticScript.alarm(), phrase: SpokenPhrase(text: "Stop!"), pan: 0)
        }
        try await Task.sleep(for: .milliseconds(200))

        let ms = await elapsedMs {
            arbiter.cancelHazard()
            await presenting.value
        }
        XCTAssertLessThan(ms, 50)
    }

    func testCancellingTheCallerCancelsThePresentation() async throws {
        let arbiter = OutputArbiter(haptics: FakeHaptics(durationMs: 60_000), speech: FakeSpeech(durationMs: 60_000))
        let presenting = Task {
            await arbiter.presentHazard(haptic: HapticScript.alarm(), phrase: SpokenPhrase(text: "Stop!"), pan: 0)
        }
        try await Task.sleep(for: .milliseconds(100))

        let ms = await elapsedMs {
            presenting.cancel()
            await presenting.value
        }
        XCTAssertLessThan(ms, 50)
    }

    func testANewHazardStopsTheOldOneFirst() async throws {
        let haptics = FakeHaptics(durationMs: 60_000)
        let arbiter = OutputArbiter(haptics: haptics, speech: FakeSpeech(durationMs: 60_000))
        let first = Task { await arbiter.presentHazard(haptic: HapticScript.alarm(), phrase: nil, pan: 0) }
        try await Task.sleep(for: .milliseconds(50))
        let second = Task {
            await arbiter.presentHazard(haptic: HapticScript.hazard(symbol: .left, urgency: 2), phrase: nil, pan: 0)
        }

        let ms = await elapsedMs { await first.value }
        XCTAssertLessThan(ms, 100)
        arbiter.cancelHazard()
        await second.value
        XCTAssertEqual(haptics.played.map(\.symbol), [.alarm, .left])
    }

    func testAHazardCutsAnAnswerMidSentence() async throws {
        let speech = FakeSpeech(durationMs: 2_000)
        let arbiter = OutputArbiter(haptics: FakeHaptics(durationMs: 100), speech: speech)
        let answer = Task { await arbiter.say(SpokenPhrase(text: "To your right, restroom sign"), on: .answer) }
        try await Task.sleep(for: .milliseconds(100))

        await arbiter.presentHazard(haptic: HapticScript.alarm(), phrase: nil, pan: 0)

        let finished = await answer.value
        XCTAssertFalse(finished)
    }

    func testNavigationIsDroppedNotQueuedWhileAHazardPlays() async throws {
        let speech = FakeSpeech(durationMs: 500)
        let arbiter = OutputArbiter(haptics: FakeHaptics(), speech: speech)
        let hazard = Task {
            await arbiter.presentHazard(haptic: nil, phrase: SpokenPhrase(text: "Behind you!"), pan: 0)
        }
        try await Task.sleep(for: .milliseconds(50))

        let spoke = await arbiter.say(SpokenPhrase(text: "Left now"), on: .navigation)

        XCTAssertFalse(spoke)
        await hazard.value
        XCTAssertEqual(speech.spoken.map(\.text), ["Behind you!"])
    }

    func testNothingStartsInsideTheSpeechGapAfterHazardSpeech() async {
        let clock = TestClock()
        let speech = FakeSpeech(durationMs: 10)
        let arbiter = OutputArbiter(haptics: FakeHaptics(), speech: speech, clock: { clock.now })
        await arbiter.presentHazard(haptic: nil, phrase: SpokenPhrase(text: "Stop!"), pan: 0)

        clock.advance(1_000)
        let navigation = await arbiter.say(SpokenPhrase(text: "Left in 30 meters"), on: .navigation)
        XCTAssertFalse(navigation)

        clock.advance(900)
        let answer = await arbiter.say(SpokenPhrase(text: "Restroom sign"), on: .answer)
        XCTAssertTrue(answer)
    }

    func testAnAnswerWaitsForTheGapThenSpeaks() async throws {
        let clock = TestClock()
        let speech = FakeSpeech(durationMs: 10)
        let arbiter = OutputArbiter(haptics: FakeHaptics(), speech: speech, clock: { clock.now })
        await arbiter.presentHazard(haptic: nil, phrase: SpokenPhrase(text: "Stop!"), pan: 0)

        let answer = Task { await arbiter.say(SpokenPhrase(text: "Restroom sign"), on: .answer, maxWaitMs: 15_000) }
        try await Task.sleep(for: .milliseconds(150))
        XCTAssertEqual(speech.spoken.count, 1)
        clock.advance(2_000)

        let spoke = await answer.value
        XCTAssertTrue(spoke)
        XCTAssertEqual(speech.spoken.map(\.text), ["Stop!", "Restroom sign"])
    }

    func testANewerAnswerReplacesAPrompt() async throws {
        let speech = FakeSpeech(durationMs: 1_000)
        let arbiter = OutputArbiter(haptics: FakeHaptics(), speech: speech)
        let prompt = Task { await arbiter.say(SpokenPhrase(text: "Hold still, looking around"), on: .prompt) }
        try await Task.sleep(for: .milliseconds(50))

        let answered = await arbiter.say(SpokenPhrase(text: "No bathroom in view"), on: .answer)

        let promptFinished = await prompt.value
        XCTAssertFalse(promptFinished)
        XCTAssertTrue(answered)
    }

    func testSpeakingIsObservableForMicGating() async {
        let seen = Recorder()
        let arbiter = OutputArbiter(haptics: FakeHaptics(), speech: FakeSpeech(durationMs: 50))
        arbiter.observeSpeaking { seen.append($0) }

        await arbiter.say(SpokenPhrase(text: "Hello"), on: .prompt)

        XCTAssertEqual(seen.values, [true, false])
        XCTAssertFalse(arbiter.isSpeaking)
    }
}

final class OutputSinkTests: XCTestCase {
    func testUrgencyThreeIsTheAlarmPlusTheLocativeWord() throws {
        let plan = OutputSink.plan(for: try Make.announcement(level: .immediate, cls: .cyclist, bearingDeg: 178),
                                   hapticsAvailable: true, userGain: 1, stereoRoute: true)
        XCTAssertEqual(plan.haptic?.symbol, .alarm)
        XCTAssertEqual(plan.phrase?.text, "Behind you!")
    }

    func testDegradedHapticsSpeakEveryUrgencyAndPlayNothing() throws {
        let plan = OutputSink.plan(for: try Make.announcement(level: .aware, bearingDeg: 45),
                                   hapticsAvailable: false, userGain: 1, stereoRoute: true)
        XCTAssertNil(plan.haptic)
        XCTAssertEqual(plan.phrase?.text, "Ahead to the right, person")
        XCTAssertEqual(plan.pan, 0)
    }

    func testAnExpiredAnnouncementPresentsNothing() async throws {
        let haptics = FakeHaptics()
        let sink = OutputSink(arbiter: OutputArbiter(haptics: haptics, speech: FakeSpeech()),
                              clock: { Make.t0 + 60_000 })

        await sink.present(try Make.announcement(level: .actSoon))

        XCTAssertTrue(haptics.played.isEmpty)
    }

    func testTheSinkPresentsThroughTheHazardChannel() async throws {
        let haptics = FakeHaptics()
        let speech = FakeSpeech(durationMs: 10)
        let sink = OutputSink(arbiter: OutputArbiter(haptics: haptics, speech: speech), clock: { Make.t0 })

        await sink.present(try Make.announcement(level: .actSoon, cls: .car, bearingDeg: -100))

        XCTAssertEqual(haptics.played.map(\.symbol), [.left])
        XCTAssertEqual(speech.spoken.map(\.text), ["To your left, car"])
    }
}

final class Recorder: @unchecked Sendable {
    private let lock = NSLock()
    private var _values: [Bool] = []
    var values: [Bool] { lock.withLock { _values } }
    func append(_ v: Bool) { lock.withLock { _values.append(v) } }
}
