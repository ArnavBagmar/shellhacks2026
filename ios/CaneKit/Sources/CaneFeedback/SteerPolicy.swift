/// Every number step 24 needs, in one place (`docs/shepherd-steering-plan.md`
/// §3: "one table, no magic numbers in code").
///
/// The two rows the plan hands us verbatim are `INTEGRATOR_K` and
/// `FAILSAFE_DECAY_MS`; `STEER_TTL_MS` is not repeated here because
/// `SteerLimits.ttlMs` already owns it on the transport side and one number
/// may only live in one file. The rest -- the quiet threshold and the pulse
/// rate range -- the plan does not specify, and they are step 24's to choose:
/// see each constant for the reasoning and `docs/steps/step-24-brief.md` for
/// what is still unproven on a wearer.
public enum SteerPolicy {
    // MARK: - Plan §3, verbatim

    /// Shepherd's stage 8. `out += (target - out) * k` per tick: a 3 Hz
    /// sensing rate becomes a continuous cue (plan §1.3). At this value a step
    /// change is 35% rendered after one tick and 90% after six (535 ms).
    public static let integratorK = 0.35
    /// The tick `k` is quoted against. The app already runs a 100 ms feedback
    /// tick (`Runtime.tickMs`), which is the loop the integrator rides; the
    /// maths is elapsed-time-correct, so a jittering tick does not retune it.
    public static let integratorTickMs: Int64 = 100
    /// Shepherd's stage 9: their ESP32 ramps motor power to zero over ~500 ms
    /// when the link drops. Same guarantee, for a wearer instead of a motor.
    public static let failsafeDecayMs: Int64 = 500

    // MARK: - Step 24's own choices

    /// Plan risk 3: "a continuous cue competes with speech for the wearer's
    /// attention ... step 24 must keep it quiet when `proximityFactor` is
    /// low, or the demo becomes noise." Below this the cue does not exist.
    ///
    /// 0.35 on the §2.3 ramp (`d_clear` 3.0 m, `d_stop` 0.6 m) is 2.16 m --
    /// about 1.5 s of walking at 1.4 m/s, and roughly three cane sweeps out.
    /// Nearer than that and the wearer wants to know; further and the hazard
    /// path (which sees *objects*) is the right channel.
    public static let quietProximity = 0.35
    /// Hysteresis, for the reason `Hysteresis` exists for urgency: a proximity
    /// sitting on the threshold must not chatter the cue on and off. Once
    /// audible, the cue keeps playing down to here.
    public static let quietProximityRelease = 0.30

    /// Slowest pulse, at the quiet threshold: ~one pulse per walking step.
    public static let pulseIntervalMaxMs: Int64 = 700
    /// Fastest pulse, at the stopping distance. Well clear of the ~60 ms
    /// fusion limit `HapticAlphabet.minInterOnsetMs` already respects, and a
    /// whole multiple of the tick so the rate the wearer feels is the rate
    /// asked for: the pulse clock can only fire on a tick, so an interval
    /// that is not a multiple of `integratorTickMs` is rounded up in practice.
    public static let pulseIntervalMinMs: Int64 = 200

    /// Plan §2.3: with the ground-plane fit rejected, `proximity` is a band
    /// and not a number, so the rate may only take band-many values -- the
    /// same "trust the band, not the metre" rule `RangeTrust` applies to phone
    /// ranges. Three bands: far, closing, stop.
    public static let unanchoredBandCount = 3

    /// How long one navigation turn cue is allowed to keep pulling on the
    /// steering mix. A turn cue is momentary where Shepherd's GPS bias was
    /// continuous, so it is held for a few seconds and then expires -- a cue
    /// from two blocks ago must not still be leaning on the wearer.
    public static let navigationBiasTtlMs: Int64 = 3_000

    // MARK: - The two mappings

    /// The quiet gate, with hysteresis. `wasAudible` is the cue's own last
    /// state, so this is a pure function of (proximity, previous state).
    public static func isAudible(proximity: Double, wasAudible: Bool) -> Bool {
        proximity >= (wasAudible ? quietProximityRelease : quietProximity)
    }

    /// Plan step 24: "haptic pulse rate = proximity". Nearer is faster.
    ///
    /// Anchored, this is a continuous ramp from `pulseIntervalMaxMs` at the
    /// quiet threshold to `pulseIntervalMinMs` at the stopping distance.
    /// Unanchored, it is quantised to `unanchoredBandCount` rates, because a
    /// number we do not have must not be rendered as one.
    public static func pulseIntervalMs(proximity: Double, anchored: Bool) -> Int64 {
        let span = pulseIntervalMaxMs - pulseIntervalMinMs
        guard anchored else {
            // Bands over the whole 0...1 range, not over the audible part:
            // an unanchored proximity is a coarse judgement to begin with.
            let bands = Double(unanchoredBandCount)
            let band = min(bands - 1, (max(0, proximity) * bands).rounded(.down))
            let step = Double(span) / (bands - 1)
            return pulseIntervalMaxMs - Int64((band * step).rounded())
        }
        let audibleSpan = 1.0 - quietProximity
        let fraction = min(max((proximity - quietProximity) / audibleSpan, 0), 1)
        return pulseIntervalMaxMs - Int64((fraction * Double(span)).rounded())
    }
}
