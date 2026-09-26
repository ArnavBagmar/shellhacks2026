import Foundation
import os

/// Plays one encoded clip (MP3) to its end; `stop()` ends it at once.
public protocol ClipPlayer: Sendable {
    /// False when the clip could not be played -- the caller falls back.
    func play(_ audio: Data) async -> Bool
    /// Synchronous, idempotent, safe when idle.
    func stop()
}

/// What happened to one phrase, for the black-box recorder.
public enum NaturalVoiceOutcome: Sendable, Equatable {
    /// Played in the natural voice; `waitedMs` from request to first sound.
    case natural(waitedMs: Int)
    /// Spoken by the phone's own voice instead.
    case fallback(reason: String)
}

/// Answers and prompts in the natural voice (ElevenLabs, fetched through the
/// laptop), and the phone's own voice whenever that is not there in time:
/// no link, no key, an API error, or more than `fetchBudgetMs` of waiting.
/// The wearer always hears the words; at worst in the other voice.
///
/// Never used for hazards: those must be instant and work offline.
public final class NaturalSpeechRenderer: SpeechRenderer, @unchecked Sendable {
    /// Turbo v2.5 answered in 0.26 s from the laptop (2026-09-26); the rest
    /// of the budget is the Wi-Fi hop both ways, with room for a slow one.
    public static let defaultFetchBudgetMs = 2_500

    private let fallback: SpeechRenderer
    private let player: ClipPlayer
    private let fetch: @Sendable (String) async -> Data?
    private let fetchBudgetMs: Int
    private let onOutcome: @Sendable (String, NaturalVoiceOutcome) -> Void
    private let lock = NSLock()
    private var generation: UInt64 = 0
    private var abandonFetch: (@Sendable () -> Void)?
    private let logger = Logger(subsystem: "cane", category: "natural-voice")

    public init(
        fallback: SpeechRenderer,
        player: ClipPlayer,
        fetchBudgetMs: Int = NaturalSpeechRenderer.defaultFetchBudgetMs,
        onOutcome: @escaping @Sendable (String, NaturalVoiceOutcome) -> Void = { _, _ in },
        fetch: @escaping @Sendable (String) async -> Data?
    ) {
        self.fallback = fallback
        self.player = player
        self.fetch = fetch
        self.fetchBudgetMs = fetchBudgetMs
        self.onOutcome = onOutcome
    }

    public func speak(_ phrase: SpokenPhrase) async {
        guard !Task.isCancelled else { return }
        let mine = lock.withLock { () -> UInt64 in
            generation += 1
            return generation
        }
        let started = ContinuousClock.now
        let audio = await withTaskCancellationHandler {
            await fetchWithinBudget(phrase.text)
        } onCancel: {
            self.stop()
        }
        guard isCurrent(mine) else { return }
        if let audio {
            let waited = Self.ms(since: started)
            if await player.play(audio) {
                onOutcome(phrase.text, .natural(waitedMs: waited))
                return
            }
            guard isCurrent(mine) else { return }
            onOutcome(phrase.text, .fallback(reason: "clip would not play"))
        } else {
            onOutcome(phrase.text, .fallback(reason: "no clip within \(fetchBudgetMs) ms"))
        }
        await fallback.speak(phrase)
    }

    public func stop() {
        let abandon = lock.withLock { () -> (@Sendable () -> Void)? in
            generation += 1
            defer { abandonFetch = nil }
            return abandonFetch
        }
        abandon?()
        player.stop()
        fallback.stop()
    }

    private func isCurrent(_ token: UInt64) -> Bool {
        lock.withLock { generation == token } && !Task.isCancelled
    }

    /// The clip, or nil once the budget runs out or `stop()` is called --
    /// whichever is first. The fetch itself is left to finish on its own (a
    /// late clip still lands in the cache for next time).
    private func fetchWithinBudget(_ text: String) async -> Data? {
        await withCheckedContinuation { (continuation: CheckedContinuation<Data?, Never>) in
            let once = ResumeOnce(continuation)
            let fetch = fetch
            let fetchTask = Task { once.resume(await fetch(text)) }
            let budget = fetchBudgetMs
            let timer = Task {
                try? await Task.sleep(for: .milliseconds(budget))
                once.resume(nil)
            }
            lock.withLock {
                abandonFetch = {
                    once.resume(nil)
                    timer.cancel()
                    _ = fetchTask
                }
            }
        }
    }

    private static func ms(since start: ContinuousClock.Instant) -> Int {
        let d = ContinuousClock.now - start
        return Int(d.components.seconds * 1000 + d.components.attoseconds / 1_000_000_000_000_000)
    }
}

/// Resumes a continuation exactly once, from whichever caller gets there first.
final class ResumeOnce<T: Sendable>: @unchecked Sendable {
    private let lock = NSLock()
    private var continuation: CheckedContinuation<T, Never>?

    init(_ continuation: CheckedContinuation<T, Never>) {
        self.continuation = continuation
    }

    func resume(_ value: T) {
        let c = lock.withLock { () -> CheckedContinuation<T, Never>? in
            defer { continuation = nil }
            return continuation
        }
        c?.resume(returning: value)
    }
}
