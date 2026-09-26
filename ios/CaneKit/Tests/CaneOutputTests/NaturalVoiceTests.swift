import Foundation
import XCTest

@testable import CaneOutput

final class FakeClipPlayer: ClipPlayer, @unchecked Sendable {
    private let lock = NSLock()
    private let clocked = FakeClockedRenderer()
    private var _played: [Data] = []
    private var stopped = false
    let durationMs: Int
    let works: Bool

    init(durationMs: Int = 100, works: Bool = true) {
        self.durationMs = durationMs
        self.works = works
    }

    var played: [Data] { lock.withLock { _played } }

    func play(_ audio: Data) async -> Bool {
        guard works else { return false }
        lock.withLock {
            _played.append(audio)
            stopped = false
        }
        await clocked.run(durationMs: durationMs)
        return lock.withLock { !stopped }
    }

    func stop() {
        lock.withLock { stopped = true }
        clocked.stop()
    }
}

final class Outcomes: @unchecked Sendable {
    private let lock = NSLock()
    private var _all: [NaturalVoiceOutcome] = []
    var all: [NaturalVoiceOutcome] { lock.withLock { _all } }
    func add(_ outcome: NaturalVoiceOutcome) { lock.withLock { _all.append(outcome) } }
}

final class NaturalSpeechRendererTests: XCTestCase {
    private let clip = Data("ID3 mp3".utf8)

    func testAFetchedClipIsPlayedInsteadOfTheOwnVoice() async {
        let player = FakeClipPlayer(), fallback = FakeSpeech(), outcomes = Outcomes()
        let clip = clip
        let renderer = NaturalSpeechRenderer(fallback: fallback, player: player, onOutcome: { outcomes.add($1) }) { _ in clip }

        await renderer.speak(SpokenPhrase(text: "Paris."))

        XCTAssertEqual(player.played, [clip])
        XCTAssertTrue(fallback.spoken.isEmpty)
        guard case .natural = outcomes.all.first else { return XCTFail("\(outcomes.all)") }
    }

    func testNoClipMeansTheOwnVoice() async {
        let player = FakeClipPlayer(), fallback = FakeSpeech()
        let renderer = NaturalSpeechRenderer(fallback: fallback, player: player) { _ in nil }

        await renderer.speak(SpokenPhrase(text: "Paris."))

        XCTAssertTrue(player.played.isEmpty)
        XCTAssertEqual(fallback.spoken.map(\.text), ["Paris."])
    }

    func testASlowClipIsNotWaitedForPastTheBudget() async {
        let player = FakeClipPlayer(), fallback = FakeSpeech(durationMs: 10)
        let clip = clip
        let renderer = NaturalSpeechRenderer(fallback: fallback, player: player, fetchBudgetMs: 100) { _ in
            try? await Task.sleep(for: .seconds(5))
            return clip
        }

        let ms = await elapsedMs { await renderer.speak(SpokenPhrase(text: "Paris.")) }

        XCTAssertLessThan(ms, 400)
        XCTAssertEqual(fallback.spoken.map(\.text), ["Paris."])
        XCTAssertTrue(player.played.isEmpty)
    }

    func testAClipThatWillNotPlayFallsBackToTheOwnVoice() async {
        let fallback = FakeSpeech()
        let clip = clip
        let renderer = NaturalSpeechRenderer(fallback: fallback, player: FakeClipPlayer(works: false)) { _ in clip }

        await renderer.speak(SpokenPhrase(text: "Paris."))

        XCTAssertEqual(fallback.spoken.map(\.text), ["Paris."])
    }

    func testStopWhileFetchingReturnsAtOnceAndSaysNothing() async throws {
        let player = FakeClipPlayer(), fallback = FakeSpeech()
        let clip = clip
        let renderer = NaturalSpeechRenderer(fallback: fallback, player: player, fetchBudgetMs: 5_000) { _ in
            try? await Task.sleep(for: .seconds(2))
            return clip
        }
        let speaking = Task { await renderer.speak(SpokenPhrase(text: "Paris.")) }
        try await Task.sleep(for: .milliseconds(50))

        let ms = await elapsedMs {
            renderer.stop()
            await speaking.value
        }

        XCTAssertLessThan(ms, 100)
        XCTAssertTrue(player.played.isEmpty)
        XCTAssertTrue(fallback.spoken.isEmpty)
    }
}

final class NaturalVoiceRoutingTests: XCTestCase {
    func testAnswersAndPromptsUseTheNaturalVoiceHazardsAndTurnsNever() async throws {
        let own = FakeSpeech(durationMs: 10), natural = FakeSpeech(durationMs: 10)
        let arbiter = OutputArbiter(haptics: FakeHaptics(durationMs: 10), speech: own, naturalSpeech: natural)

        await arbiter.say(SpokenPhrase(text: "Let me check."), on: .prompt)
        await arbiter.say(SpokenPhrase(text: "Paris."), on: .answer)
        await arbiter.say(SpokenPhrase(text: "Turn left."), on: .navigation)
        try await Task.sleep(for: .milliseconds(20))
        await arbiter.presentHazard(haptic: nil, phrase: SpokenPhrase(text: "Stop!"), pan: 0)

        XCTAssertEqual(natural.spoken.map(\.text), ["Let me check.", "Paris."])
        XCTAssertEqual(own.spoken.map(\.text), ["Turn left.", "Stop!"])
    }

    func testAHazardCutsANaturalVoiceAnswer() async throws {
        let natural = FakeSpeech(durationMs: 60_000)
        let arbiter = OutputArbiter(haptics: FakeHaptics(durationMs: 10), speech: FakeSpeech(durationMs: 10),
                                    naturalSpeech: natural)
        let answering = Task { await arbiter.say(SpokenPhrase(text: "A long answer."), on: .answer) }
        try await Task.sleep(for: .milliseconds(30))

        await arbiter.presentHazard(haptic: nil, phrase: SpokenPhrase(text: "Stop!"), pan: 0)
        let finished = await answering.value

        XCTAssertFalse(finished)
    }
}
