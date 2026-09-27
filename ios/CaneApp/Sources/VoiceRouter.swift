import CaneAssist
import CaneNavigation
import CaneOutput
import CaneVoice
import Foundation
import os

/// Open-mic transcripts -> the wake-word gate -> an intent -> whoever owns
/// it. An actor because transcripts arrive on the audio thread while the
/// gate's stability timer ticks on another.
actor VoiceRouter {
    private var gate = WakeWordGate()
    private var phrases = PromptPicker()
    private let arbiter: OutputArbiter
    private let coordinator: QueryCoordinator
    private let navigate: @Sendable (String) async -> Void
    /// Indoors or outdoors right now, or nil before GPS has said. Decides who
    /// owns a destination: the laptop's sign memory, or the Routes engine.
    private let setting: @Sendable () -> WalkSetting?
    private let onHeard: @Sendable (String) -> Void
    private let diagnostics: Diagnostics
    private let logger = Logger(subsystem: "cane", category: "voice-router")

    init(
        arbiter: OutputArbiter,
        coordinator: QueryCoordinator,
        navigate: @escaping @Sendable (String) async -> Void,
        setting: @escaping @Sendable () -> WalkSetting? = { nil },
        onHeard: @escaping @Sendable (String) -> Void,
        diagnostics: Diagnostics
    ) {
        self.diagnostics = diagnostics
        self.arbiter = arbiter
        self.coordinator = coordinator
        self.navigate = navigate
        self.setting = setting
        self.onHeard = onHeard
    }

    func transcript(_ text: String, isFinal: Bool, nowMs: Int64) {
        handle(gate.feed(text: text, isFinal: isFinal, nowMs: nowMs))
    }

    func tick(nowMs: Int64) {
        handle(gate.tick(nowMs: nowMs))
    }

    /// The typed path from the setup screen: exactly as if spoken after the
    /// wake word.
    func command(_ text: String) {
        diagnostics.record("typed", ["text": .text(text)])
        handle(.command(IntentParser.normalize(text)))
    }

    private func handle(_ output: WakeWordGate.Output) {
        switch output {
        case .none:
            return
        case .wakeOnly:
            diagnostics.record("gate", ["result": .text("wake-only")])
            onHeard("Hey Cane")
            say(phrases.next(.listening))
        case .command(let text):
            onHeard(text)
            logger.info("heard: \(text, privacy: .public)")
            let intent = IntentParser.route(text)
            diagnostics.record("intent", [
                "command": .text(text),
                "intent": .text(intent.map { String(describing: $0) } ?? "none"),
            ])
            let arbiter = arbiter, coordinator = coordinator, navigate = navigate, onHeard = onHeard
            let setting = setting
            Task {
                let understood = await Self.route(
                    text, arbiter: arbiter, coordinator: coordinator, navigate: navigate, setting: setting
                )
                if !understood {
                    // Show exactly what was heard (the screen is the only
                    // way to debug this), and let the wearer just repeat it.
                    onHeard("\(text)  (not understood)")
                    self.rearm()
                }
            }
        }
    }

    private func rearm() {
        gate.arm(nowMs: Int64(Date().timeIntervalSince1970 * 1_000))
    }

    /// Returns false when the words matched no intent.
    private static func route(
        _ text: String, arbiter: OutputArbiter, coordinator: QueryCoordinator,
        navigate: @Sendable (String) async -> Void,
        setting: @Sendable () -> WalkSetting? = { nil }
    ) async -> Bool {
        guard let intent = IntentParser.route(text) else {
            logger.info("not understood: \(text, privacy: .public)")
            await arbiter.say(SpokenPhrase(text: AnswerPhrasing.notUnderstood(heard: text)), on: .prompt, maxWaitMs: 3_000)
            return false
        }
        if await coordinator.handle(intent, utterance: text) { return true }
        switch intent {
        case .navigate(let place):
            // "Take me to room 100" inside a building is not a Maps question:
            // Places would resolve it to the building the wearer is already
            // standing in. Indoors the laptop's sign memory is the only thing
            // that knows where room 100 is, so the destination becomes a find.
            // Outdoors -- and before GPS has said anything -- Maps owns it.
            if setting() == .indoors {
                logger.info("indoors: \(place, privacy: .public) goes to the sign engine, not Maps")
                await coordinator.handle(.find(place), utterance: "where is \(place)")
            } else {
                await navigate(place)
            }
        case .time:
            await arbiter.say(SpokenPhrase(text: AnswerPhrasing.time()), on: .prompt, maxWaitMs: 3_000)
        case .help:
            await arbiter.say(SpokenPhrase(text: AnswerPhrasing.help), on: .prompt, maxWaitMs: 3_000)
        case .readSign:
            // Step 13 reads text on the phone frame; until it lands, ask the
            // laptop to describe what is ahead, which reads signs too.
            await coordinator.handle(.describeFront, utterance: "read the sign in front of me")
        default:
            break
        }
        return true
    }

    private static let logger = Logger(subsystem: "cane", category: "voice-router")

    private func say(_ text: String) {
        let arbiter = arbiter
        Task { await arbiter.say(SpokenPhrase(text: text), on: .prompt, maxWaitMs: 3_000) }
    }
}
