#if os(iOS)
import AVFoundation
import Foundation
import os

/// `AVSpeechSynthesizer` behind a `speak` that cannot hang and cannot trap
/// (step-10 design Amendment 3):
/// - a resume-once guard (`didFinish` and `didCancel` can both arrive);
/// - a watchdog: `stopSpeaking` fires no delegate when nothing is actually
///   speaking, and a missing `didStart` must not hold the caller forever;
/// - `stopSpeaking(at: .immediate)`, never `.word`;
/// - one pinned voice for the session, primed at startup so the first real
///   hazard does not pay the voice-asset load.
public final class AVSpeechRenderer: NSObject, SpeechRenderer, AVSpeechSynthesizerDelegate, @unchecked Sendable {
    public static let defaultRate: Float = 0.55
    public static let rateRange: ClosedRange<Float> = 0.45...0.70
    /// No `didStart` within this long -> give up on the utterance.
    static let startWatchdogMs = 1_500
    /// Hard cap on one utterance, whatever its length.
    static let maxUtteranceMs = 12_000

    private let synthesizer = AVSpeechSynthesizer()
    private let voice: AVSpeechSynthesisVoice?
    private let rate: @Sendable () -> Float
    private let lock = NSLock()
    private var pending: (utterance: AVSpeechUtterance, continuation: CheckedContinuation<Void, Never>)?
    private var started = false
    private let logger = Logger(subsystem: "cane", category: "speech")

    public init(language: String = "en-US", rate: @escaping @Sendable () -> Float = { AVSpeechRenderer.defaultRate }) {
        self.voice = Self.bestVoice(language: language)
        self.rate = rate
        super.init()
        synthesizer.delegate = self
        prime()
    }

    public func speak(_ phrase: SpokenPhrase) async {
        guard !Task.isCancelled else { return }
        let utterance = AVSpeechUtterance(string: phrase.text)
        utterance.voice = voice
        utterance.rate = min(max(rate(), Self.rateRange.lowerBound), Self.rateRange.upperBound)
        utterance.pitchMultiplier = phrase.pitchMultiplier
        utterance.volume = 1.0
        utterance.preUtteranceDelay = 0
        utterance.postUtteranceDelay = 0
        await withTaskCancellationHandler {
            await withCheckedContinuation { (continuation: CheckedContinuation<Void, Never>) in
                lock.withLock {
                    pending = (utterance, continuation)
                    started = false
                }
                synthesizer.speak(utterance)
                scheduleWatchdogs(for: utterance)
            }
        } onCancel: {
            self.stop()
        }
    }

    public func stop() {
        synthesizer.stopSpeaking(at: .immediate)
        resume(nil)
    }

    private func scheduleWatchdogs(for utterance: AVSpeechUtterance) {
        Task { [weak self] in
            try? await Task.sleep(for: .milliseconds(Self.startWatchdogMs))
            guard let self, self.lock.withLock({ self.pending?.utterance === utterance && !self.started }) else { return }
            self.logger.warning("speech never started; releasing the caller")
            self.resume(utterance)
        }
        Task { [weak self] in
            try? await Task.sleep(for: .milliseconds(Self.maxUtteranceMs))
            self?.resume(utterance)
        }
    }

    /// Resume-once: only the continuation for `utterance` (or any, if nil).
    private func resume(_ utterance: AVSpeechUtterance?) {
        let continuation = lock.withLock { () -> CheckedContinuation<Void, Never>? in
            guard let current = pending else { return nil }
            if let utterance, current.utterance !== utterance { return nil }
            pending = nil
            return current.continuation
        }
        continuation?.resume()
    }

    public func speechSynthesizer(_ synthesizer: AVSpeechSynthesizer, didStart utterance: AVSpeechUtterance) {
        lock.withLock { if pending?.utterance === utterance { started = true } }
    }

    public func speechSynthesizer(_ synthesizer: AVSpeechSynthesizer, didFinish utterance: AVSpeechUtterance) {
        resume(utterance)
    }

    public func speechSynthesizer(_ synthesizer: AVSpeechSynthesizer, didCancel utterance: AVSpeechUtterance) {
        resume(utterance)
    }

    /// One silent utterance so the voice asset is loaded before a hazard.
    private func prime() {
        let utterance = AVSpeechUtterance(string: " ")
        utterance.voice = voice
        utterance.volume = 0
        synthesizer.speak(utterance)
    }

    /// Premium, then enhanced, then default quality, for the language;
    /// never force-unwrapped.
    static func bestVoice(language: String) -> AVSpeechSynthesisVoice? {
        let voices = AVSpeechSynthesisVoice.speechVoices().filter { $0.language == language }
        for quality in [AVSpeechSynthesisVoiceQuality.premium, .enhanced, .default] {
            if let voice = voices.first(where: { $0.quality == quality }) { return voice }
        }
        return AVSpeechSynthesisVoice(language: language)
    }
}
#endif
