import CaneFeedback
import CaneTransport

/// The words for a `ground` cue, and the cue's whole output plan.
///
/// **"drop", not "step down".** `SpeechPhrase`'s rule is that every phrase the
/// device speaks is locative or a self-contained imperative and *never* a
/// steering command, because the system does not model free space and must not
/// tell the wearer which way to move. "Step down" fails that test twice over:
/// it is an instruction, and it is an instruction to do the exact thing that
/// hurts if the hole is deeper than a step. "Ahead to the left, drop" says
/// where it is and lets the cane do what the cane is good at.
///
/// **No number, ever.** The cue carries a ratio, not a depth, because Depth
/// Anything V2 is affine-invariant in disparity. There is deliberately no code
/// path here that can render `dropRatio` into speech.
public enum GroundPhrase {
    public static let hazardWord = "drop"

    /// Direction first, like every other phrase the device speaks (WCAG 3.2.4
    /// consistent identification, and it survives being cut off by a Stop).
    public static func words(for cue: GroundCue) -> String {
        "\(BearingSector.phrase(cue.bearingDeg)), \(hazardWord)"
    }

    /// A ground cue rides the hazard channel at the urgency-2 texture: a
    /// directional haptic plus a couple of words. Never the urgency-3 alarm --
    /// a hole is not closing on you, and the PRD is explicit that this channel
    /// announces at 1-2 and never as a Stop.
    public static func plan(
        for cue: GroundCue,
        hapticsAvailable: Bool,
        userGain: Float,
        route: PanRoute,
        channels: Int
    ) -> OutputSink.Plan {
        let symbol = HapticSymbol.directional(for: BearingSector.sector(cue.bearingDeg))
        let haptic = hapticsAvailable
            ? HapticScript.hazard(symbol, level: .actSoon, userGain: Double(userGain))
            : nil
        return OutputSink.Plan(
            haptic: haptic,
            phrase: SpokenPhrase(text: words(for: cue)),
            pan: haptic == nil
                ? 0
                : StereoPan.pan(bearingDeg: cue.bearingDeg, route: route, channelCount: channels)
        )
    }
}
