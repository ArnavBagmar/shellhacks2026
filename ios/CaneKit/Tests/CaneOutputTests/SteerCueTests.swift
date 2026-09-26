import CaneFeedback
import Foundation
import XCTest

@testable import CaneOutput

/// Records what was panned where, so the steering cue's stereo position is
/// assertable without an audio engine.
final class RecordingEarcon: EarconRenderer, @unchecked Sendable {
    private let lock = NSLock()
    private var storedPans: [Double] = []
    var pans: [Double] { lock.withLock { storedPans } }
    func play(pan: Double) { lock.withLock { storedPans.append(pan) } }
}

/// Step 24's cue as a value: the pattern, the tier and the pan. Shepherd's
/// stage 8 drove a motor; we drive a wearer, so the only question that matters
/// here is whether this can be confused with anything they already know.
final class SteerHapticTests: XCTestCase {

    /// The whole point of the pattern: one dull tick, repeated. Every hazard
    /// letter and every navigation cue is a *burst* of 1-9 elements spanning
    /// 180-900 ms whose shape carries the message; this one carries its
    /// message in the repetition rate and the side, so it must not look like
    /// any of them.
    func testTheSteeringTickIsUnlikeEveryHazardAndNavigationPattern() throws {
        let tick = HapticScript.steering()
        XCTAssertEqual(tick.elements.count, 1, "one element: it cannot be counted against left (2) or right (3)")
        XCTAssertEqual(tick.elements.first?.kind, .transient)
        for script in HapticAlphabet.all where script.symbol != .steerTick {
            XCTAssertNotEqual(tick.elements, script.elements, "collides with \(script.symbol)")
        }
        // Shorter than every letter in the alphabet, so it never occupies the
        // actuator when a hazard needs it.
        for script in HapticAlphabet.all where script.symbol != .steerTick {
            XCTAssertLessThan(tick.totalMs, script.totalMs, "\(script.symbol)")
        }
        // A dull thud, not the sharp click the lateral hazard letters use.
        let sharpness = try XCTUnwrap(tick.elements.first?.sharpness)
        XCTAssertLessThan(sharpness, HapticAlphabet.script(.left).elements[0].sharpness)
        for script in HapticAlphabet.all where script.symbol != .steerTick {
            for element in script.elements where element.kind == .transient {
                XCTAssertNotEqual(sharpness, element.sharpness, "\(script.symbol)")
            }
        }
    }

    /// Three tiers, in one order, at every user gain: steering < navigation <
    /// the softest hazard. The wearer can always tell which channel is
    /// talking by how hard it hits.
    func testTheSteeringTierIsQuieterThanNavigationAndEveryHazardAtEveryGain() throws {
        for gain in stride(from: 0.6, through: 1.4, by: 0.1) {
            let steering = try XCTUnwrap(HapticScript.steering(userGain: gain).elements.first?.baseIntensity)
            let navigation = try XCTUnwrap(HapticScript.navigation(side: .left, userGain: gain)?
                .elements.map(\.baseIntensity).min())
            let softestHazard = HapticScript.hazard(.ahead, level: .aware, userGain: gain)
                .elements.map(\.baseIntensity).min()!
            XCTAssertLessThan(steering, navigation, "gain \(gain)")
            XCTAssertLessThan(navigation, softestHazard, "gain \(gain)")
        }
    }

    /// The tier is fixed: only the *rate* and the *side* carry information, so
    /// a user-gain change can never turn the steering cue into a hazard.
    func testTheSteeringIntensityStaysInsideItsOwnBandForAnyGain() {
        for gain in [-10.0, 0.0, 0.6, 1.0, 1.4, 99.0] {
            let intensity = HapticScript.steering(userGain: gain).elements[0].baseIntensity
            XCTAssertGreaterThanOrEqual(intensity, HapticScript.steeringMinIntensity)
            XCTAssertLessThanOrEqual(intensity, HapticScript.steeringMaxIntensity)
        }
    }

    /// `HapticSymbol.symbol` is the only thing that picks a hazard letter, and
    /// it must never pick the steering tick -- that would put a state signal
    /// where a warning belongs.
    func testNoHazardCanEverSelectTheSteeringTick() {
        for bearing in stride(from: -180.0, through: 180.0, by: 5) {
            let sector = BearingSector.sector(bearing)
            for level in [AnnouncementLevel.aware, .actSoon, .immediate] {
                XCTAssertNotEqual(HapticSymbol.symbol(level: level, sector: sector), .steerTick)
            }
            XCTAssertNotEqual(HapticSymbol.directional(for: sector), .steerTick)
        }
    }
}

final class SteerCuePlanTests: XCTestCase {
    private func cue(lateral: Double, proximity: Double = 1.0) -> SteerCue {
        SteerCue(lateral: lateral, proximity: proximity, intervalMs: 200)
    }

    private func plan(lateral: Double, route: PanRoute = .headphones, channels: Int = 2,
                      hapticsAvailable: Bool = true) -> SteerCueOutput.Plan {
        SteerCueOutput.plan(for: cue(lateral: lateral), hapticsAvailable: hapticsAvailable,
                            userGain: 1.0, route: route, channels: channels)
    }

    /// Plan step 24: stereo pan = steer angle. Full scale is the edge of the
    /// forward ring (plan §3's -90...+90), and `BearingSector`'s sign
    /// convention is reused rather than re-derived: negative is the left.
    func testThePanFollowsTheSteerAngleWithTheSameSignConventionAsEverythingElse() {
        XCTAssertEqual(plan(lateral: 0).pan, 0)
        XCTAssertLessThan(plan(lateral: -0.5).pan, 0)
        XCTAssertGreaterThan(plan(lateral: 0.5).pan, 0)
        XCTAssertEqual(plan(lateral: -0.5).pan, -plan(lateral: 0.5).pan, accuracy: 1e-12)
        XCTAssertEqual(SteerCueOutput.bearingDeg(forLateral: 1.0), SteerCueOutput.ringEdgeDeg)
        XCTAssertEqual(SteerCueOutput.bearingDeg(forLateral: -1.0), -SteerCueOutput.ringEdgeDeg)
    }

    func testThePanIsMonotoneInTheSteerAngleAndNeverExceedsTheCap() {
        var previous = -Double.infinity
        for step in -20...20 {
            let lateral = Double(step) / 20
            let pan = plan(lateral: lateral).pan
            XCTAssertGreaterThanOrEqual(pan, previous, "lateral \(lateral)")
            XCTAssertLessThanOrEqual(abs(pan), StereoPan.panCap)
            XCTAssertGreaterThanOrEqual(StereoPan.minChannelGain(pan), StereoPan.minChannelGainFloor)
            previous = pan
        }
    }

    /// Amendment 4's route gate is reused, not re-implemented: with one bud or
    /// on the built-in speaker there is no pan, so the haptic carries the side
    /// alone -- exactly as it does for a hazard.
    func testThereIsNoPanOnARouteThatCannotCarryOne() {
        XCTAssertEqual(plan(lateral: 1.0, route: .builtInSpeaker, channels: 2).pan, 0)
        XCTAssertEqual(plan(lateral: 1.0, route: .headphones, channels: 1).pan, 0)
        XCTAssertNotEqual(plan(lateral: 1.0, route: .bluetoothA2DP, channels: 2).pan, 0)
    }

    /// No Taptic Engine means no steering cue at all. It is deliberately not
    /// spoken: a continuous cue rendered as speech is the chatty third channel
    /// plan risk 3 says ruins the demo.
    func testWithoutHapticsThereIsNoSteeringCueAndNothingIsSpokenInstead() {
        let plan = plan(lateral: 1.0, hapticsAvailable: false)
        XCTAssertNil(plan.haptic)
        XCTAssertEqual(plan.pan, 0)
        XCTAssertFalse(plan.isPlayable)
    }
}

/// Acceptance 4: the steering cue is the lowest thing in the house. Pinned
/// against the *existing* arbitration so step 24 cannot regress it.
final class SteerArbitrationTests: XCTestCase {

    func testTheChannelOrderPutsSteeringBelowEverythingIncludingNavigation() {
        XCTAssertLessThan(OutputChannel.steering, OutputChannel.navigation)
        XCTAssertLessThan(OutputChannel.navigation, OutputChannel.prompt)
        XCTAssertLessThan(OutputChannel.prompt, OutputChannel.answer)
        XCTAssertLessThan(OutputChannel.answer, OutputChannel.hazard)
    }

    /// Nothing about the speech priority changed: a hazard still cuts an
    /// answer mid-sentence and a navigation cue is still dropped, not queued.
    func testHazardSpeechStillPreemptsEverythingAndNavigationIsStillDropped() async throws {
        let speech = FakeSpeech(durationMs: 2_000)
        let arbiter = OutputArbiter(haptics: FakeHaptics(), speech: speech)

        let answer = Task { await arbiter.say(SpokenPhrase(text: "To your right, restroom sign"), on: .answer) }
        try await Task.sleep(for: .milliseconds(80))
        let hazard = Task {
            await arbiter.presentHazard(haptic: nil, phrase: SpokenPhrase(text: "Behind you!"), pan: 0)
        }
        try await Task.sleep(for: .milliseconds(80))

        let navigation = await arbiter.say(SpokenPhrase(text: "Left now"), on: .navigation)
        let steering = await arbiter.steerPulse(haptic: HapticScript.steering(), pan: 0.3)

        let answered = await answer.value
        XCTAssertFalse(answered, "the hazard cut the answer")
        XCTAssertFalse(navigation, "a navigation cue under a hazard is dropped, not queued")
        XCTAssertFalse(steering, "the steering cue never interrupts a hazard")
        arbiter.cancelHazard()
        await hazard.value
        XCTAssertEqual(speech.spoken.map(\.text), ["To your right, restroom sign", "Behind you!"])
    }

    /// A steering pulse already playing when a hazard arrives is cut, and the
    /// hazard does not wait for it.
    func testAHazardCutsASteeringPulseInFlight() async throws {
        let haptics = FakeHaptics(durationMs: 60_000)
        let arbiter = OutputArbiter(haptics: haptics, speech: FakeSpeech(durationMs: 10))
        let pulse = Task { await arbiter.steerPulse(haptic: HapticScript.steering(), pan: 0.2) }
        try await Task.sleep(for: .milliseconds(80))

        let ms = await elapsedMs {
            await arbiter.presentHazard(haptic: nil, phrase: SpokenPhrase(text: "Stop!"), pan: 0)
        }

        XCTAssertLessThan(ms, 200, "the hazard did not wait for the steering cue")
        let played = await pulse.value
        XCTAssertFalse(played)
        XCTAssertEqual(haptics.played.map(\.symbol), [.steerTick])
    }

    /// The 1800 ms gap after hazard speech applies to the steering cue too:
    /// the wearer gets the street back between warnings.
    func testTheSteeringCueStaysOutOfTheGapAfterHazardSpeech() async {
        let clock = TestClock()
        let arbiter = OutputArbiter(haptics: FakeHaptics(), speech: FakeSpeech(durationMs: 10), clock: { clock.now })
        await arbiter.presentHazard(haptic: nil, phrase: SpokenPhrase(text: "Stop!"), pan: 0)

        clock.advance(1_000)
        let insideGap = await arbiter.steerPulse(haptic: HapticScript.steering(), pan: 0.2)
        XCTAssertFalse(insideGap)
        clock.advance(900)
        let afterGap = await arbiter.steerPulse(haptic: HapticScript.steering(), pan: 0.2)
        XCTAssertTrue(afterGap)
    }

    /// The panned tick rides with the pulse, on the channel the wearer's
    /// headphones can actually place.
    func testAPulseWithAPanPlaysThePannedEarcon() async {
        let earcon = RecordingEarcon()
        let arbiter = OutputArbiter(haptics: FakeHaptics(), speech: FakeSpeech(durationMs: 10), earcon: earcon)
        await arbiter.steerPulse(haptic: HapticScript.steering(), pan: -0.25)
        XCTAssertEqual(earcon.pans, [-0.25])
    }

    /// The sink is the seam: `pulse` plays, `silence` stops, and neither ever
    /// blocks the feedback manager's tick.
    func testTheSinkPlaysAPulseAndStopsOnSilence() async throws {
        let haptics = FakeHaptics()
        let earcon = RecordingEarcon()
        let arbiter = OutputArbiter(haptics: haptics, speech: FakeSpeech(durationMs: 10), earcon: earcon)
        let sink = SteerCueOutput(arbiter: arbiter, panRoute: { (.headphones, 2) })

        sink.pulse(SteerCue(lateral: 0.8, proximity: 0.9, intervalMs: 200))
        for _ in 0..<200 where haptics.played.isEmpty {
            try await Task.sleep(for: .milliseconds(5))
        }
        XCTAssertEqual(haptics.played.map(\.symbol), [.steerTick])
        XCTAssertEqual(earcon.pans.count, 1)
        XCTAssertGreaterThan(try XCTUnwrap(earcon.pans.first), 0)

        sink.silence()
        XCTAssertEqual(haptics.played.count, 1, "silence plays nothing")
    }
}
