import Foundation

/// The engine seams. Real implementations live behind `#if os(iOS)`
/// (`CoreHapticsRenderer`, `AVSpeechRenderer`, `EarconPlayer`); tests use
/// fakes, so every timing and cancellation rule is proven on macOS.

public protocol HapticRenderer: Sendable {
    /// False when the device has no Taptic Engine or the engine will not
    /// start -- the sink then speaks every announcement, urgency 1 included.
    var isAvailable: Bool { get }
    /// Plays one script, stopping any script already playing first (two
    /// CoreHaptics players superimpose into mush). Returns when the script
    /// ends or `stop()` is called.
    func play(_ script: HapticScript) async
    /// Synchronous, idempotent, safe when idle.
    func stop()
}

public protocol SpeechRenderer: Sendable {
    /// Returns when the utterance finishes or `stop()` is called -- never
    /// hangs (the real one has a watchdog).
    func speak(_ phrase: SpokenPhrase) async
    /// Synchronous, idempotent, safe when idle. Stops immediately, not at a
    /// word boundary.
    func stop()
}

/// A 40 ms panned tick played alongside the haptic -- the only thing that
/// is ever panned. Fire and forget; its failure costs nothing.
public protocol EarconRenderer: Sendable {
    func play(pan: Double)
}

public struct SilentEarcon: EarconRenderer {
    public init() {}
    public func play(pan: Double) {}
}
