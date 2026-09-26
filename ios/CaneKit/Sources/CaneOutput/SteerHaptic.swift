import CaneFeedback
import Foundation

/// The steering cue's gain tier (step 24, `docs/shepherd-steering-plan.md`).
///
/// Three tiers on one actuator, in one order, at every user gain:
/// **steering < navigation < the softest hazard**. The wearer can tell which
/// channel is talking by how hard it hits, before decoding anything.
///
/// The steering tier sits *below* `HapticAlphabet.intensityFloor`, and that is
/// deliberate: the floor exists because step 9 writes a `TrackRecord` at
/// presentation start, so an unfelt urgency-1 hazard cue suppresses the next
/// warning for eight seconds. A steering pulse records nothing and another one
/// follows within 700 ms, so a missed one costs nothing -- and a cue at hazard
/// strength, several times a second, is exactly the noise plan risk 3 warns
/// the demo away from.
extension HapticScript {
    /// Nominal tier at `userGain == 1`.
    public static let steeringGain = 0.35
    /// Never weaker than this, or the cue is decorative.
    public static let steeringMinIntensity = 0.25
    /// Never stronger than this, which keeps it under
    /// `HapticScript.navigationGain` at every setting in
    /// `HapticAlphabet.userGainRange`.
    public static let steeringMaxIntensity = 0.45

    /// One pulse of the continuous steering cue, at the played intensity (the
    /// renderer plays `baseIntensity` as-is, same contract as `hazard` and
    /// `navigation`).
    ///
    /// Only the *rate* and the *side* carry information, so the intensity does
    /// not vary with proximity: a cue that both sped up and got harder would
    /// read as two overlapping messages, and the weakest pulses would be the
    /// ones the wearer needed to feel through a coat.
    public static func steering(userGain: Double = HapticAlphabet.defaultUserGain) -> HapticScript {
        let gain = clampedUserGain(userGain)
        let intensity = min(max(steeringGain * gain, steeringMinIntensity), steeringMaxIntensity)
        return HapticAlphabet.script(.steerTick).scaled { _ in intensity }
    }
}
