import Foundation

/// Who is allowed to talk. One synthesizer and one Taptic Engine are shared
/// by four producers, in this priority order.
public enum OutputChannel: Int, Comparable, Sendable {
    /// Turn-by-turn cues. Dropped, never queued, while anything above talks:
    /// a late turn cue is a wrong cue (runbook "Demo decisions" #2).
    case navigation
    /// Short acknowledgements: "Hold still, looking around."
    case prompt
    /// The answer to a spoken question (Gemini). Waits, up to its TTL.
    case answer
    /// Feedback-manager announcements. Preempt everything, always.
    case hazard

    public static func < (a: OutputChannel, b: OutputChannel) -> Bool { a.rawValue < b.rawValue }
}

/// Owns the renderers and decides, per request, whether it plays now,
/// waits, or is dropped. Hazards preempt everything; nothing preempts a
/// hazard; nothing below a hazard starts inside the 1800 ms speech gap after
/// hazard speech (step-10 design R1), so the wearer's one open ear gets the
/// street back between warnings.
///
/// A class with a lock rather than an actor: `cancelHazard()` must be
/// synchronous (the `AnnouncementSink` contract), and the renderers' `stop()`
/// is what makes an in-flight presentation return.
public final class OutputArbiter: @unchecked Sendable {
    public static let speechLeadMs = SpeechSettings.speechLeadMs
    public static let hazardSpeechGapMs: Int64 = 1_800
    static let waitPollMs = 50

    public let haptics: HapticRenderer
    private let speech: SpeechRenderer
    /// Answers and prompts, when set (the ElevenLabs voice via the laptop).
    /// Hazards and navigation always use `speech`: instant and offline.
    private let naturalSpeech: SpeechRenderer?
    private let earcon: EarconRenderer
    private let clock: @Sendable () -> Int64

    private struct Active {
        let token: UInt64
        let channel: OutputChannel
        let task: Task<Void, Never>
    }

    private let lock = NSLock()
    private var active: Active?
    private var nextToken: UInt64 = 0
    private var lastHazardSpeechEndMs: Int64?
    private var speakingCount = 0
    private var speakingObserver: (@Sendable (Bool) -> Void)?
    private var phraseObserver: (@Sendable (String) -> Void)?

    public init(
        haptics: HapticRenderer,
        speech: SpeechRenderer,
        naturalSpeech: SpeechRenderer? = nil,
        earcon: EarconRenderer = SilentEarcon(),
        clock: @escaping @Sendable () -> Int64 = { Int64(Date().timeIntervalSince1970 * 1000) }
    ) {
        self.haptics = haptics
        self.speech = speech
        self.naturalSpeech = naturalSpeech
        self.earcon = earcon
        self.clock = clock
    }

    /// True while an utterance is actually being spoken. The microphone
    /// listener mutes on this, or it would hear the phone say "cyclist".
    public var isSpeaking: Bool {
        lock.withLock { speakingCount > 0 }
    }

    /// Called on every change of `isSpeaking`, on an arbitrary thread.
    public func observeSpeaking(_ observer: @escaping @Sendable (Bool) -> Void) {
        lock.withLock { speakingObserver = observer }
    }

    /// Called with the text of every utterance as it starts (for captions).
    public func observePhrases(_ observer: @escaping @Sendable (String) -> Void) {
        lock.withLock { phraseObserver = observer }
    }

    // MARK: - Hazards

    /// Preempts whatever is playing and presents one hazard. Returns when it
    /// finishes or is cancelled (by `cancelHazard()` or by cancelling the
    /// calling task).
    public func presentHazard(haptic: HapticScript?, phrase: SpokenPhrase?, pan: Double) async {
        guard !Task.isCancelled else { return }
        let token = lock.withLock { () -> UInt64 in
            interruptActiveLocked()
            return startLocked(.hazard, haptic: haptic, phrase: phrase, pan: pan)
        }
        await awaitActive(token)
        if phrase != nil {
            lock.withLock { lastHazardSpeechEndMs = clock() }
        }
    }

    /// Synchronous, idempotent, safe when idle: stops a hazard in flight.
    public func cancelHazard() {
        lock.withLock {
            guard active?.channel == .hazard else { return }
            interruptActiveLocked()
        }
    }

    // MARK: - Everything else

    /// Speaks `phrase` on `channel` (never `.hazard`). Navigation is dropped
    /// if it cannot start at once; prompts and answers wait up to `maxWaitMs`
    /// for the hazard and its gap to clear. Returns whether it was presented
    /// to the end.
    @discardableResult
    public func say(
        _ phrase: SpokenPhrase?,
        on channel: OutputChannel,
        haptic: HapticScript? = nil,
        pan: Double = 0,
        maxWaitMs: Int = 0
    ) async -> Bool {
        precondition(channel != .hazard, "hazards go through presentHazard")
        let deadline = clock() + Int64(maxWaitMs)
        while true {
            if Task.isCancelled { return false }
            let token = lock.withLock { () -> UInt64? in
                guard canStartLocked(channel) else { return nil }
                interruptActiveLocked()
                return startLocked(channel, haptic: haptic, phrase: phrase, pan: pan)
            }
            if let token {
                return await awaitActive(token)
            }
            if channel == .navigation || clock() >= deadline { return false }
            try? await Task.sleep(for: .milliseconds(Self.waitPollMs))
        }
    }

    /// Stops whatever is playing on `channel` or below (never a hazard).
    public func cancel(upTo channel: OutputChannel) {
        lock.withLock {
            guard let active, active.channel != .hazard, active.channel <= channel else { return }
            interruptActiveLocked()
        }
    }

    // MARK: - Internals (callers hold `lock` for *Locked)

    private func canStartLocked(_ channel: OutputChannel) -> Bool {
        if let active, active.channel == .hazard || active.channel > channel { return false }
        if let end = lastHazardSpeechEndMs, clock() - end < Self.hazardSpeechGapMs { return false }
        return true
    }

    private func interruptActiveLocked() {
        guard let current = active else { return }
        current.task.cancel()
        active = nil
        haptics.stop()
        speech.stop()
        naturalSpeech?.stop()
    }

    private func startLocked(_ channel: OutputChannel, haptic: HapticScript?, phrase: SpokenPhrase?, pan: Double) -> UInt64 {
        nextToken += 1
        let token = nextToken
        let renderer = (channel == .answer || channel == .prompt) ? (naturalSpeech ?? speech) : speech
        let task = Task<Void, Never> { [weak self] in
            await self?.perform(haptic: haptic, phrase: phrase, pan: pan, renderer: renderer)
        }
        active = Active(token: token, channel: channel, task: task)
        return token
    }

    /// Waits for presentation `token`; cancelling the caller cancels it.
    /// Returns true when it ran to the end.
    @discardableResult
    private func awaitActive(_ token: UInt64) async -> Bool {
        guard let task = lock.withLock({ active?.token == token ? active?.task : nil }) else { return false }
        await withTaskCancellationHandler {
            await task.value
        } onCancel: {
            self.lock.withLock {
                guard self.active?.token == token else { return }
                self.interruptActiveLocked()
            }
        }
        return lock.withLock { () -> Bool in
            guard active?.token == token else { return false }
            active = nil
            return !task.isCancelled
        }
    }

    /// Haptic at t=0, earcon alongside, speech at t=120 ms (Amendment 3):
    /// the body cue primes attention so the first phoneme -- the direction --
    /// is not missed. Total time is max(haptic, 120 + speech), not the sum.
    private func perform(haptic: HapticScript?, phrase: SpokenPhrase?, pan: Double, renderer: SpeechRenderer) async {
        await withTaskGroup(of: Void.self) { group in
            if let haptic {
                group.addTask { await self.haptics.play(haptic) }
                if pan != 0 { earcon.play(pan: pan) }
            }
            if let phrase {
                group.addTask {
                    if haptic != nil {
                        try? await Task.sleep(for: .milliseconds(Self.speechLeadMs))
                    }
                    guard !Task.isCancelled else { return }
                    self.lock.withLock { self.phraseObserver }?(phrase.text)
                    self.setSpeaking(true)
                    await renderer.speak(phrase)
                    self.setSpeaking(false)
                }
            }
        }
    }

    private func setSpeaking(_ speaking: Bool) {
        let (changed, observer) = lock.withLock { () -> (Bool, (@Sendable (Bool) -> Void)?) in
            let before = speakingCount > 0
            speakingCount = max(0, speakingCount + (speaking ? 1 : -1))
            return (before != (speakingCount > 0), speakingObserver)
        }
        if changed { observer?(speaking) }
    }
}
