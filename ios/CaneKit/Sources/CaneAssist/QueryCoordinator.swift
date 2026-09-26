import CaneOutput
import CaneTransport
import CaneVoice
import Foundation
import os

/// Sends one `query` frame. `EventLinkClient` is the real one.
public protocol QueryLink: Sendable {
    func send(_ query: SceneQuery) async throws
}

extension EventLinkClient: QueryLink {}

/// Speaks on a non-hazard channel. `OutputArbiter` is the real one.
public protocol AssistSpeaker: Sendable {
    func say(_ phrase: SpokenPhrase?, on channel: OutputChannel, haptic: HapticScript?, pan: Double, maxWaitMs: Int) async -> Bool
    func cancel(upTo channel: OutputChannel)
}

extension OutputArbiter: AssistSpeaker {}

/// "Hey Cane, is there a bathroom around?" from intent to spoken answer.
///
/// 1. send the `query` at once (the laptop's settle timer starts now), with
///    a front-camera photo when one is available;
/// 2. meanwhile say "Hold still, looking around." -- the ~1 s it takes to
///    say is the settle time;
/// 3. speak the matching `answer` on the answer channel (hazards still cut
///    in), or a timeout line if none arrives.
///
/// Only the newest question counts: an answer for any other `queryId` is
/// dropped, and a new question supersedes the one in flight.
public actor QueryCoordinator {
    public enum State: Equatable, Sendable {
        case idle
        case looking(queryId: String)
        case answering
    }

    /// Laptop worst case: 0.4 s settle + ~1 s frame + 15 s model budget.
    public static let answerTimeoutMs = 20_000
    /// One reassurance line while Gemini is slow, so silence is never
    /// mistaken for a dead link.
    public static let stillLookingMs = 7_000
    public static let stillLooking = "Still looking."
    public static let answerMaxWaitMs = 15_000
    public static let promptMaxWaitMs = 3_000

    private let link: QueryLink
    private let speaker: AssistSpeaker
    private let snapshot: @Sendable () -> PhoneFrame?
    private let clock: @Sendable () -> Int64
    private let makeId: @Sendable () -> String
    private let userGain: @Sendable () -> Float
    private let timeoutMs: Int
    private let logger = Logger(subsystem: "cane", category: "assist")

    public private(set) var state: State = .idle
    private var target: String?
    private var timeoutTask: Task<Void, Never>?
    private var stateObserver: (@Sendable (State) -> Void)?

    public init(
        link: QueryLink,
        speaker: AssistSpeaker,
        snapshot: @escaping @Sendable () -> PhoneFrame? = { nil },
        clock: @escaping @Sendable () -> Int64 = { Int64(Date().timeIntervalSince1970 * 1000) },
        makeId: @escaping @Sendable () -> String = { "q-" + UUID().uuidString.prefix(8).lowercased() },
        userGain: @escaping @Sendable () -> Float = { 1.0 },
        timeoutMs: Int = QueryCoordinator.answerTimeoutMs
    ) {
        self.link = link
        self.speaker = speaker
        self.snapshot = snapshot
        self.clock = clock
        self.makeId = makeId
        self.userGain = userGain
        self.timeoutMs = timeoutMs
    }

    public func observeState(_ observer: @escaping @Sendable (State) -> Void) {
        stateObserver = observer
    }

    /// The intents this type owns: find, describe around/front, ask, cancel.
    /// Returns false for any other intent (the caller routes it).
    @discardableResult
    public func handle(_ intent: VoiceIntent, utterance: String? = nil) async -> Bool {
        switch intent {
        case .find(let target):
            await ask(.find, target: target, utterance: utterance ?? "is there a \(target) around")
        case .describeAround:
            await ask(.describeAround, target: nil, utterance: utterance ?? "what is around me")
        case .describeFront:
            await ask(.describeFront, target: nil, utterance: utterance ?? "what is in front of me")
        case .cancel:
            finish()
            speaker.cancel(upTo: .answer)
            _ = await speaker.say(SpokenPhrase(text: AnswerPhrasing.cancelled), on: .prompt, haptic: nil, pan: 0, maxWaitMs: 0)
        case .ask(let question):
            await ask(.ask, target: nil, utterance: question)
        case .readSign, .navigate, .time, .help:
            return false
        }
        return true
    }

    public func receive(_ answer: SceneAnswer) async {
        guard state == .looking(queryId: answer.queryId) else {
            logger.info("dropping answer \(answer.queryId, privacy: .public): not the current question")
            return
        }
        timeoutTask?.cancel()
        setState(.answering)
        let text = AnswerPhrasing.phrase(for: answer, target: target)
        let haptic = HapticScript.navigation(side: AnswerPhrasing.side(for: answer), userGain: Double(userGain()))
        logger.info("answer \(answer.queryId, privacy: .public) \(answer.status.rawValue, privacy: .public) in \(answer.latencyMs) ms: \(text, privacy: .public)")
        speaker.cancel(upTo: .prompt)
        _ = await speaker.say(SpokenPhrase(text: text), on: .answer, haptic: haptic, pan: 0, maxWaitMs: Self.answerMaxWaitMs)
        if state == .answering { setState(.idle) }
    }

    // MARK: - Internals

    private func ask(_ kind: SceneQueryKind, target: String?, utterance: String) async {
        finish()
        let id = makeId()
        let query: SceneQuery
        do {
            query = try SceneQuery(
                queryId: id, kind: kind, target: target, utterance: String(utterance.prefix(300)),
                tPhoneMs: clock(), phoneFrame: snapshot()
            )
        } catch {
            logger.error("could not build query: \(String(describing: error), privacy: .public)")
            return
        }
        // Recorded before sending: `send` suspends, and the answer must
        // find this question current however fast it comes back.
        self.target = target
        setState(.looking(queryId: id))
        do {
            try await link.send(query)
        } catch {
            logger.warning("query not sent: \(String(describing: error), privacy: .public)")
            if state == .looking(queryId: id) { finish() }
            _ = await speaker.say(SpokenPhrase(text: AnswerPhrasing.noLink), on: .prompt, haptic: nil, pan: 0,
                                  maxWaitMs: Self.promptMaxWaitMs)
            return
        }
        guard state == .looking(queryId: id) else { return }
        startTimeout(for: id)
        let prompt = switch kind {
        case .describeFront: AnswerPhrasing.lookingAhead
        case .ask: AnswerPhrasing.checking
        case .find, .describeAround: AnswerPhrasing.holdStill
        }
        _ = await speaker.say(SpokenPhrase(text: prompt), on: .prompt, haptic: nil, pan: 0, maxWaitMs: Self.promptMaxWaitMs)
    }

    private func startTimeout(for id: String) {
        let total = timeoutMs
        let nudge = min(Self.stillLookingMs, total)
        timeoutTask = Task { [weak self] in
            try? await Task.sleep(for: .milliseconds(nudge))
            guard !Task.isCancelled else { return }
            await self?.stillLooking(id)
            try? await Task.sleep(for: .milliseconds(total - nudge))
            guard !Task.isCancelled else { return }
            await self?.timedOut(id)
        }
    }

    private func stillLooking(_ id: String) async {
        guard state == .looking(queryId: id), timeoutMs > Self.stillLookingMs else { return }
        _ = await speaker.say(SpokenPhrase(text: Self.stillLooking), on: .prompt, haptic: nil, pan: 0, maxWaitMs: 0)
    }

    private func timedOut(_ id: String) async {
        guard state == .looking(queryId: id) else { return }
        logger.warning("no answer for \(id, privacy: .public) within \(self.timeoutMs) ms")
        setState(.idle)
        _ = await speaker.say(SpokenPhrase(text: AnswerPhrasing.timedOut), on: .answer, haptic: nil, pan: 0,
                              maxWaitMs: Self.promptMaxWaitMs)
    }

    private func finish() {
        timeoutTask?.cancel()
        timeoutTask = nil
        target = nil
        setState(.idle)
    }

    private func setState(_ next: State) {
        guard next != state else { return }
        state = next
        stateObserver?(next)
    }
}
