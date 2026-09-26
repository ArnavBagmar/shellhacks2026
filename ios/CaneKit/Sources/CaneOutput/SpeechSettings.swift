import Foundation
import CaneFeedback

/// Amendment 3 -- `AVSpeechUtterance` parameters and the timing contract,
/// kept free of AVFoundation so they can be asserted without a device.
///
/// The renderer reads these; it does not invent its own.
public enum SpeechSettings {
    /// `AVSpeechUtteranceDefaultSpeechRate` is 0.5. Blind users commonly
    /// prefer faster, but one open ear on a noisy sidewalk and a first-time
    /// listener cap how far we should go.
    public static let defaultRate: Float = 0.55
    /// User-adjustable and persisted.
    public static let rateRange: ClosedRange<Float> = 0.45...0.70

    /// A small pitch raise is a standard redundant urgency cue, and
    /// higher-pitched speech is less masked by low-frequency traffic.
    public static func pitchMultiplier(_ level: AnnouncementLevel) -> Float {
        level == .immediate ? 1.15 : 1.00
    }

    public static let volume: Float = 1.0

    /// Both zero. A pre-delay is pure latency against a 2 s TTC, and a
    /// post-delay holds the synthesizer busy, extending both the 1.2 s
    /// budget and the 50 ms cancellation budget. Inter-announcement spacing
    /// belongs to step 9's `MIN_SPEECH_GAP_MS`, not to the utterance.
    public static let preUtteranceDelay: TimeInterval = 0
    public static let postUtteranceDelay: TimeInterval = 0

    /// H3's budget. The on-device test speaks the longest legal phrase and
    /// asserts it comes in under this; Apple does not document the rate->wpm
    /// mapping, so this is calibrated by measurement, not arithmetic.
    ///
    /// Escape ladder if it fails, in order: raise `rate` toward 0.65; then
    /// lower `SpeechPhrase.maxSpokenWords` to 5. **Never shorten the §C
    /// sector words** -- they are verbatim spec and the user learns one
    /// vocabulary.
    public static let speechBudgetMs = 1200

    /// The haptic starts at t=0 and the utterance at t=120 ms. The body cue
    /// primes attention so the first phoneme is not missed -- and the first
    /// phoneme is 100% of the direction information in "Left side!".
    ///
    /// If on-device latency measurement makes 120 ms unaffordable, 0 is the
    /// documented fallback.
    public static let speechLeadMs = 120

    /// `stopSpeaking` returns `false` and fires no delegate callback when
    /// the synthesizer is not actually speaking, which would otherwise hang
    /// `present` forever. Step 9's `PRESENT_TIMEOUT_MS = 2500` is a backstop,
    /// not the primary mechanism.
    public static let speechWatchdogMs = 1500

    /// Must return within this of `cancelCurrent()`.
    public static let cancelBudgetMs = 50

    /// `max(hapticMs, lead + speechMs)` -- **not** `max(hapticMs, speechMs)`
    /// and certainly not the sum. E6's concurrency test asserts this.
    public static func presentationDurationMs(hapticMs: Int, speechMs: Int?) -> Int {
        guard let speechMs else { return hapticMs }
        return max(hapticMs, speechLeadMs + speechMs)
    }
}
