import CaneCamera
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

/// What the front camera saw when a question was asked: the photo sent to
/// the laptop, and the TrueDepth map kept here to measure the answer's box.
public struct FrontCapture: Sendable {
    public var frame: PhoneFrame?
    public var depth: DepthMap?

    public init(frame: PhoneFrame? = nil, depth: DepthMap? = nil) {
        self.frame = frame
        self.depth = depth
    }
}

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
    /// A standing search asks again this often while the wearer walks. Cheap
    /// on purpose: the laptop answers most of these out of the signs it has
    /// already read, without spending a model call.
    public static let keepWalkingRetryMs = 5_000
    /// ...and says something out loud at least this often, so the wearer never
    /// has to wonder whether the search is still alive.
    public static let reassureEveryMs = 10_000
    /// A search cannot run forever. Two minutes of walking is enough to have
    /// passed real signage; past that, saying so beats promising silently.
    public static let standingSearchMaxMs = 120_000

    private let link: QueryLink
    private let speaker: AssistSpeaker
    private let snapshot: @Sendable () -> FrontCapture
    private let clock: @Sendable () -> Int64
    private let makeId: @Sendable () -> String
    private let userGain: @Sendable () -> Float
    private let timeoutMs: Int
    private let locale: Locale
    private var phrases: PromptPicker
    private let onDistance: @Sendable (DistanceOutcome) -> Void
    private let keepSearching: @Sendable () -> Bool
    private let standingRetryMs: Int
    private let logger = Logger(subsystem: "cane", category: "assist")

    public private(set) var state: State = .idle
    private var target: String?
    /// A `find` the wearer is still owed an answer to, kept alive while they
    /// walk. Only indoors: outdoors the Maps engine owns getting them there.
    private struct StandingSearch {
        let target: String
        let startedMs: Int64
        var lastSpokeMs: Int64
    }

    private var standing: StandingSearch?
    private var standingTask: Task<Void, Never>?
    /// The depth map from the current question's photo, and whether the
    /// wearer asked "how far".
    private var depth: DepthMap?
    private var distanceAsked = false
    private var timeoutTask: Task<Void, Never>?
    private var stateObserver: (@Sendable (State) -> Void)?

    public init(
        link: QueryLink,
        speaker: AssistSpeaker,
        snapshot: @escaping @Sendable () -> FrontCapture = { FrontCapture() },
        clock: @escaping @Sendable () -> Int64 = { Int64(Date().timeIntervalSince1970 * 1000) },
        makeId: @escaping @Sendable () -> String = { "q-" + UUID().uuidString.prefix(8).lowercased() },
        userGain: @escaping @Sendable () -> Float = { 1.0 },
        timeoutMs: Int = QueryCoordinator.answerTimeoutMs,
        locale: Locale = .current,
        keepSearching: @escaping @Sendable () -> Bool = { false },
        standingRetryMs: Int = QueryCoordinator.keepWalkingRetryMs,
        onDistance: @escaping @Sendable (DistanceOutcome) -> Void = { _ in },
        choosePhrase: @escaping @Sendable (Int) -> Int = { Int.random(in: 0..<$0) }
    ) {
        self.phrases = PromptPicker(choose: choosePhrase)
        self.link = link
        self.speaker = speaker
        self.snapshot = snapshot
        self.clock = clock
        self.makeId = makeId
        self.userGain = userGain
        self.timeoutMs = timeoutMs
        self.locale = locale
        self.keepSearching = keepSearching
        self.standingRetryMs = standingRetryMs
        self.onDistance = onDistance
    }

    public func observeState(_ observer: @escaping @Sendable (State) -> Void) {
        stateObserver = observer
    }

    /// The intents this type owns: find, describe around/front, ask, cancel.
    /// Returns false for any other intent (the caller routes it).
    @discardableResult
    public func handle(_ intent: VoiceIntent, utterance: String? = nil) async -> Bool {
        // The wearer asking anything new -- including "cancel" -- retires the
        // search they were waiting on. Retries reach `ask` directly instead.
        stopStanding()
        switch intent {
        case .find(let target):
            await ask(.find, target: target, utterance: utterance ?? "is there a \(target) around")
        case .howFar(let target):
            await ask(.find, target: target, utterance: utterance ?? "how far is the \(target)", distanceAsked: true)
        case .describeAround:
            await ask(.describeAround, target: nil, utterance: utterance ?? "what is around me")
        case .describeFront:
            await ask(.describeFront, target: nil, utterance: utterance ?? "what is in front of me")
        case .cancel:
            await cancel(acknowledge: true)
        case .ask(let question):
            await ask(.ask, target: nil, utterance: question)
        case .readSign, .navigate, .time, .help, .stopNavigation, .repeatDirection, .navigationStatus:
            return false
        }
        return true
    }

    /// Drops the question in flight and cuts its speech; "Okay." unless the
    /// caller says something itself (e.g. "Navigation stopped.").
    public func cancel(acknowledge: Bool) async {
        finish()
        speaker.cancel(upTo: .answer)
        guard acknowledge else { return }
        _ = await speaker.say(SpokenPhrase(text: AnswerPhrasing.cancelled), on: .prompt, haptic: nil, pan: 0, maxWaitMs: 0)
    }

    public func receive(_ answer: SceneAnswer) async {
        guard state == .looking(queryId: answer.queryId) else {
            logger.info("dropping answer \(answer.queryId, privacy: .public): not the current question")
            return
        }
        timeoutTask?.cancel()
        // Indoors, "I don't see one" is not the end of the conversation: the
        // wearer can keep walking and the laptop keeps reading signage while
        // they do. Outdoors this is off and the plain answer is spoken.
        if answer.status == .notFound, let searchTarget = target, keepSearching() {
            await continueStandingSearch(for: searchTarget)
            return
        }
        stopStanding()
        setState(.answering)
        var distance: DistanceOutcome?
        if answer.status == .found {
            let outcome = DistanceOutcome.evaluate(box: answer.phoneBox, depth: depth)
            onDistance(outcome)
            distance = outcome
        }
        depth = nil
        let text = AnswerPhrasing.phrase(for: answer, target: target, distance: distance, asked: distanceAsked,
                                         locale: locale)
        let haptic = HapticScript.navigation(side: AnswerPhrasing.side(for: answer), userGain: Double(userGain()))
        logger.info("answer \(answer.queryId, privacy: .public) \(answer.status.rawValue, privacy: .public) in \(answer.latencyMs) ms: \(text, privacy: .public)")
        speaker.cancel(upTo: .prompt)
        _ = await speaker.say(SpokenPhrase(text: text), on: .answer, haptic: haptic, pan: 0, maxWaitMs: Self.answerMaxWaitMs)
        if state == .answering { setState(.idle) }
    }

    // MARK: - Internals

    private func ask(
        _ kind: SceneQueryKind, target: String?, utterance: String, distanceAsked: Bool = false,
        announce: Bool = true
    ) async {
        finish()
        let id = makeId()
        let capture = snapshot()
        let query: SceneQuery
        do {
            query = try SceneQuery(
                queryId: id, kind: kind, target: target, utterance: String(utterance.prefix(300)),
                tPhoneMs: clock(), phoneFrame: capture.frame
            )
        } catch {
            logger.error("could not build query: \(String(describing: error), privacy: .public)")
            return
        }
        // Recorded before sending: `send` suspends, and the answer must
        // find this question current however fast it comes back.
        self.target = target
        // Only a map that belongs to the photo sent: a box drawn on one
        // photo means nothing on another.
        self.depth = capture.frame == nil ? nil : capture.depth
        self.distanceAsked = distanceAsked
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
        let moment: PromptMoment = switch kind {
        case .describeFront: .lookAhead
        case .ask: .check
        case .find where distanceAsked: .measure
        case .find, .describeAround: .lookAround
        }
        // A standing search's re-asks are silent: "Looking around" every few
        // seconds for two minutes would be unbearable.
        guard announce else { return }
        let prompt = phrases.next(moment)
        _ = await speaker.say(SpokenPhrase(text: prompt), on: .prompt, haptic: nil, pan: 0, maxWaitMs: Self.promptMaxWaitMs)
    }

    // MARK: - A search that survives the answer

    /// Takes responsibility for a `not_found`: either keep looking as the
    /// wearer walks, or say the search is over. Never falls through to
    /// "I don't see one", which is the answer this replaces.
    private func continueStandingSearch(for searchTarget: String) async {
        let now = clock()
        guard var search = standing else {
            standing = StandingSearch(target: searchTarget, startedMs: now, lastSpokeMs: now)
            await speakStanding(AnswerPhrasing.keepWalking(target: searchTarget))
            scheduleRetry(for: searchTarget)
            return
        }
        if now - search.startedMs >= Self.standingSearchMaxMs {
            stopStanding()
            await speakStanding(AnswerPhrasing.searchGaveUp(target: searchTarget))
            return
        }
        if now - search.lastSpokeMs >= Self.reassureEveryMs {
            search.lastSpokeMs = now
            // On the prompt channel: a reassurance is the one line that is
            // better dropped than delivered late.
            _ = await speaker.say(SpokenPhrase(text: phrases.next(.stillLooking)), on: .prompt,
                                  haptic: nil, pan: 0, maxWaitMs: 0)
        }
        standing = search
        setState(.idle)
        scheduleRetry(for: searchTarget)
    }

    private func speakStanding(_ text: String) async {
        setState(.answering)
        speaker.cancel(upTo: .prompt)
        _ = await speaker.say(SpokenPhrase(text: text), on: .answer, haptic: nil, pan: 0,
                              maxWaitMs: Self.answerMaxWaitMs)
        setState(.idle)
    }

    private func scheduleRetry(for searchTarget: String) {
        standingTask?.cancel()
        let waitMs = standingRetryMs
        standingTask = Task { [weak self] in
            try? await Task.sleep(for: .milliseconds(waitMs))
            guard !Task.isCancelled else { return }
            await self?.retryStanding(searchTarget)
        }
    }

    private func retryStanding(_ searchTarget: String) async {
        guard let search = standing, search.target == searchTarget else { return }
        await ask(.find, target: searchTarget, utterance: "is there a \(searchTarget) around",
                  announce: false)
    }

    private func stopStanding() {
        standingTask?.cancel()
        standingTask = nil
        standing = nil
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
        _ = await speaker.say(SpokenPhrase(text: phrases.next(.stillLooking)), on: .prompt, haptic: nil, pan: 0, maxWaitMs: 0)
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
        depth = nil
        distanceAsked = false
        setState(.idle)
    }

    private func setState(_ next: State) {
        guard next != state else { return }
        state = next
        stateObserver?(next)
    }
}
