# Task 10 design review — Haptics + TTS + stereo panning output (ecc:a11y-architect, opus)

Scope of this review: section E of `task-10-brief.md` only. Sections A–C (runbook text, build-plan F5,
insta360feed bearing words) are verbatim spec and are preserved. Section D (the `Announcement` shape and
`AnnouncementSink` protocol from task-9-design A12/A13) is a fixed seam: **no amendment below changes it.**
Where an amendment would need a step-9 constant changed, it is filed in §4 as a recommendation for the
controller, not as a step-10 amendment.

The user model this was stress-tested against: a blind pedestrian at 1.4 m/s on a city sidewalk, **one**
AirPod in (one ear deliberately open for ambient sound), iPhone in a chest strap **under or over a shirt**,
white cane in one hand. Four acceptance requirements: (a) direction from haptics alone, no speech;
(b) "act now" vs "be aware" with no deliberation; (c) speech never masks a real-world sound they need;
(d) never flooded.

## 1. Verdict

**ACCEPT WITH AMENDMENTS (7).**

E1 (file layout), E4's microphone ruling (iPhone mic, A2DP without HFP), E5's cancellation contract, E7's
scope line and the decision to make the pure policy layer Foundation-only and macOS-testable are all
correct and ratified. The structure of E is right.

The defects are concentrated in the two places where E encodes safety-critical information in a **single
perceptual dimension**, which is the one thing a non-visual interface may never do:

1. **E3's haptic alphabet has nine symbols and three collisions.** `left` = long-short and `right` =
   short-long are exact temporal mirrors — the dominant confusion class in single-actuator tactile coding,
   because a first element attenuated by a shirt or swallowed by a heel-strike transient converts one into
   the other. `behind` (3 × 80 ms) and the urgency-3 `.burst` (3 × 80 ms transients) have **identical
   numerosity and identical element durations**; they differ only in intensity and sharpness, i.e. exactly
   the two parameters that vary with strap tension, shirt thickness and body motion. An "act now" alarm
   that can be read as "be aware", and vice versa, is a wrong-action defect, not a polish item. Amendment 1
   replaces the nine symbols with **five** and adds a testable invariant that makes urgency 3 categorically
   unmistakable.
2. **E2's phrases do not fit the 1.2 s budget and the urgency-3 words are ambiguous.** The longest legal
   urgency-2 phrase under E2 is nine words ("cyclist behind you to the left, about 12 meters, closing"),
   roughly 1.9 s at the default TTS rate — over H3's budget by 60 %. And "Left!" / "Right!" do not
   distinguish *"the hazard is on your left"* from *"move left"*. A user who resolves that the wrong way
   steps **into** the hazard. Amendments 2 and 3 fix both.
3. **Hard stereo panning is a gain attenuator, not an information channel.** With one AirPod, a fully
   panned announcement is 10–20 dB down or, on a wired bud, silent. E1's `StereoPan` is specified to
   produce ±1. Amendment 4 makes panning route-aware, caps it, and moves it off the speech path entirely.
4. **E4's session cannot fail gracefully.** `.playAndRecord` cannot be activated before microphone
   permission exists, so a permission unrelated to output silences the whole device. Nothing in E covers
   the phone-call interruption, which kills the audio session *and* the CoreHaptics engine — the cane goes
   both mute and numb, silently. Amendment 5.
5. **Nothing anchors the sign of the bearing.** E6 tests threshold magnitudes (14.9/15/60/120/165) but
   never asserts that a negative bearing is the user's **left**. A sign inversion passes every test in E6
   and mirrors every announcement the product will ever make. Amendment 6.

Two of E's rulings I want to record as explicitly correct, because they will look wrong to a reviewer:
`.burst` carrying **no** directional component is right (urgency 3 must be identified in under 150 ms; any
direction encoding lengthens it and adds confusability — the direction rides in the spoken word), and
urgency 1 vs urgency 2 being distinguished by **intensity alone** is safe *given* Amendment 1, because
urgency 2 always speaks and a 1-for-2 misread costs nothing.

## 2. Amendments (paste-ready, binding once ratified)

### Amendment 1 — Replace E3 entirely: a five-symbol haptic alphabet with a continuity rule for urgency 3

*Why:* nine symbols on one actuator is past the discrimination limit, E3's left/right are temporal mirrors,
and E3's `behind` and urgency-3 `burst` are the same three-pulse pattern at different amplitudes.

Replace E3 with:

> **The alphabet has exactly five symbols.** `HapticSymbol = .ahead | .left | .right | .behind | .alarm`.
> Urgency 1 and urgency 2 both use the four directional symbols and differ **only** in gain; urgency 3
> always uses `.alarm` and never carries direction (the spoken word carries it). Nine messages on one
> actuator is past the single-site discrimination limit; five is not.
>
> **Two texture families, so the first decision the body makes is the robust one.** Discrete taps encode
> the lateral axis; smooth buzzes encode the front/back axis. The user's whole mental model is one sentence:
>
> > "Clicks are sideways: **two** clicks, wide apart, is **left**; **three** quick clicks is **right**
> > (L before R, two before three). Smooth is front or back: a **short bump** is in front, a **long swell**
> > is behind. A long hard rattle that doesn't stop means **act now**."
>
> **Pattern table.** `onset` is relative time in ms; `tap` is `.hapticTransient`; `buzz(d)` is
> `.hapticContinuous` of duration `d` ms. `attack` / `release` are CoreHaptics' **normalized 0–1 envelope
> parameters, not seconds** — verify empirically on device, do not assume units.
>
> | symbol | elements | sharpness | attack / release | totalMs |
> |---|---|---|---|---|
> | `.ahead`  | `buzz(180) @0` | 0.35 | 0.00 / 0.10 | 180 |
> | `.left`   | `tap @0`, `tap @200` | 0.55 | — | 220 |
> | `.right`  | `tap @0`, `tap @100`, `tap @200` | 0.55 | — | 220 |
> | `.behind` | `buzz(320) @0` | 0.15 | 0.35 / 0.20 | 320 |
> | `.alarm`  | `buzz(900) @0` **plus** `tap @0,100,200,…,800` (9 taps) | 0.30 bed / 1.00 taps | 0.00 / 0.00 | 900 |
>
> `.left` and `.right` deliberately span the **same** 220 ms and share their first and last element: the
> only difference is the presence of a middle click. Detecting the presence of one extra element is a more
> robust judgment than comparing two durations, and it is not a temporal mirror, so a missed or phantom
> first element cannot turn one side into the other. `.behind` uses a slow 0.35 attack and low 0.15
> sharpness: low-frequency, slowly-rising energy is what survives cloth and couples into the sternum, and
> `behind` is the sector the user has no other channel for, so it gets the most transmissible texture.
>
> **Gain, not letters.** `intensity = clamp(base × urgencyGain × userGain, 0.35, 1.0)` with
> `urgencyGain: u1 = 0.60, u2 = 0.85`, and `.alarm` pinned at **1.00 ignoring both gains**. `userGain` is a
> persisted user setting in `[0.6, 1.4]`, default 1.0. The 0.35 floor exists because an unfelt urgency-1
> haptic is worse than none: step 9 writes the `TrackRecord` at presentation *start*, so
> `refractory(1) = 8000` then suppresses that object for eight seconds on the strength of a cue the user
> never received.
>
> **The urgency-3 rule (the one the user is taught, and a unit-tested invariant):** *if it is still going,
> it is urgent.* Formally, `HapticScript.validate()` asserts, over the whole alphabet:
> `.alarm.totalMs == 900`; every non-`.alarm` script has `totalMs <= 420` and contains no continuous
> element longer than 400 ms; every non-`.alarm` element has `intensity <= 0.85 × userGain`; `.alarm` is the
> only script containing more than three elements; every inter-onset gap is `>= 60 ms` (below ~50 ms two
> torso pulses fuse); and no two scripts in the alphabet are equal. The test suite enumerates
> `HapticSymbol × urgency` and asserts all of it. The user never counts or compares anything to recognise
> urgency 3 — they notice that it has not stopped, which is the single most reliable tactile judgment
> available and is self-reinforcing (the longer it runs the more certain they are).
>
> **Sector → symbol is a coarse 4-way reduction, distinct from the §C speech bands.** `|b| < 15` →
> `.ahead`; `15 ≤ |b| < 120` → `.left` / `.right` by sign; `|b| ≥ 120` → `.behind`. Speech keeps the full
> five-band §C vocabulary. Rationale: the haptic answers "which way do I move", and "ahead to the left" and
> "to your left" imply the same movement; "behind you to the left" implies "hold your line", which is the
> `behind` message.
>
> **One player at a time.** `HapticRenderer.play` implies `stop()` of any in-flight player. Two CoreHaptics
> patterns played concurrently superimpose into an uninterpretable blend; on preemption the old alarm and
> the new one must not sum.
>
> **Engine lifecycle.** `CHHapticEngine` with `playsHapticsOnly = true` (do not contend with the shared
> audio session) and `isAutoShutdownEnabled = false` while a walk is active (auto-shutdown adds tens of ms
> to the first play after silence, which is every hazard). Implement `resetHandler` and `stoppedHandler` and
> restart on `.audioSessionInterrupt`, `.applicationSuspended`, `.idleTimeout`, `.systemError`; also rebuild
> on `AVAudioSession.mediaServicesWereResetNotification`.
>
> **Fail audibly, never silently.** If `CHHapticEngine.capabilitiesForHardware().supportsHaptics == false`
> or `start()` throws, the sink speaks once — "Haptics unavailable, speech only" — sets a
> `hapticsDegraded` flag, and continues speaking every announcement including urgency 1 (which normally
> never speaks). A mobility aid that has lost its primary channel must say so.
>
> **Two required affordances, owned by step 10's module even though the screen is step 13/14's:**
> (i) `HapticReference.playAlphabet()` — a self-test that plays all five symbols with spoken labels, to be
> run before every walk, which is also the per-session calibration of `userGain`; (ii) `HapticTrial`, the
> acceptance harness in Amendment 7.
>
> **Plan B, pre-authorised so nobody redesigns this at 2 a.m.:** if the Amendment 7 protocol shows any
> left↔right confusion, change `.right` to `tap @0,150,300` (`totalMs` 320) so the two symbols differ in
> total duration as well as density, re-run, and record the result. Change nothing else.

### Amendment 2 — Replace E2's urgency-2 template and urgency-3 word table

*Why:* E2's longest legal phrase is nine words (~1.9 s), over H3's 1.2 s budget; and "Left!"/"Right!" are
indistinguishable from steering commands, which is the one error that puts the user in the road.

Replace the E2 urgency-2 and urgency-3 bullets with:

> **Direction first, at every urgency level.** Template:
> `"\(sectorWords), \(classWord)"` then `" fast"` iff `closingMps > 3.0` (reuse step 9's
> `FAST_CLOSING_MPS`, do not introduce a knob) then `", \(rangeWords)"` iff the sector is `.ahead`
> (`|b| < 15`) **and** `cls` is a static class. The word "closing" is never spoken: it costs ~250 ms of
> masking to say what the urgency level already said.
>
> Four reasons direction leads: (i) urgency 3 preempts urgency 2 by design, so any phrase can be truncated
> mid-word — direction-first means the user has already received the part that determines the action;
> (ii) the haptic plays first and encodes direction, so the word confirms what the body just felt instead
> of contradicting the order; (iii) it makes urgency 2 and urgency 3 **one consistent vocabulary**, both
> beginning with the side (WCAG 3.2.4 Consistent Identification); (iv) class-first puts the alarming word
> ("cyclist") first and users act on it before the direction arrives. The topicalised form — "Behind you,
> cyclist." "To your left, cyclist." "Straight ahead, pole, about 4 meters." — is natural English.
>
> **`MAX_SPOKEN_WORDS = 6`, enforced by dropping in priority order: range, then "fast". The sector words
> and the class word are never dropped.** So "behind you to the left, cyclist fast" (7) renders as
> "behind you to the left, cyclist" (6).
>
> **Urgency-3 word table over all eight sectors (H2). `.oneWord` means "≤ 2 words, ≤ 450 ms", not literally
> one word:**
>
> | sector | words |
> |---|---|
> | `.ahead`, `.aheadLeft`, `.aheadRight` | `"Stop!"` |
> | `.left` | `"Left side!"` |
> | `.right` | `"Right side!"` |
> | `.behindLeft`, `.behindRight`, `.behind` | `"Behind you!"` |
>
> Every entry is **locative or self-contained-imperative, never a steering command**. The system does not
> model free space, so it must never tell the user which way to move; "Left side!" states where the hazard
> is, "Left!" does not. The one extra word costs ~180 ms and removes a coin-flip wrong-action risk.
> "Stop!" remains correct for the ahead family because A7 caps static classes at urgency 2, so urgency 3
> ahead is always a moving class closing head-on with TTC < 2 s.
>
> **Range rendering defence.** `.aboutMetres(n)` with `n < 1` renders as `"close"`, never "about 0 meters"
> — a spoken zero reads as impact. Keep `"about 1 meter"` singular. Never a decimal. `.band` words
> unchanged (`close` / `nearby` / `far`).

### Amendment 3 — `AVSpeechUtterance` parameters, voice selection, and a cancellation contract that cannot hang

*Why:* E2 sets no rate, so the default rate blows the 1.2 s budget; and the obvious implementation of
`stop()` either crashes on a double continuation resume or hangs forever, which step 9 would see as a
2500 ms timeout on every cancel.

Add to E2:

> **Utterance parameters.** `rate = 0.55` (`AVSpeechUtteranceDefaultSpeechRate` is 0.5; blind users
> commonly prefer faster, but one open ear on a noisy sidewalk and a first-time listener cap how far we
> should go — the knob is user-adjustable in `[0.45, 0.70]` and persisted). `pitchMultiplier = 1.00` for
> urgency 2 and **`1.15` for urgency 3** — a small pitch raise is a standard redundant urgency cue and
> higher-pitched speech is less masked by low-frequency traffic. `volume = 1.0` at both levels.
> **`preUtteranceDelay = 0` and `postUtteranceDelay = 0`**: a pre-delay is pure latency against a 2 s TTC,
> and a post-delay holds the synthesizer busy, extending both the 1.2 s budget and the 50 ms cancellation
> budget. Inter-announcement spacing belongs to step 9's `MIN_SPEECH_GAP_MS`, not to the utterance.
>
> **The rate is calibrated by measurement, not by arithmetic.** Apple does not document the rate→wpm
> mapping. Ship an on-device test that speaks the longest legal phrase and asserts duration ≤ 1.2 s. If it
> fails, the escape ladder is, in order: raise `rate` toward 0.65; then lower `MAX_SPOKEN_WORDS` to 5.
> **Never shorten the §C sector words** — they are verbatim spec and the user learns one vocabulary.
>
> **Voice.** Iterate `AVSpeechSynthesisVoice.speechVoices()` filtered to the user's language, prefer
> `.premium` then `.enhanced` then `.default`, **pin the chosen identifier for the whole session** so the
> voice never changes mid-walk, and fall back to `AVSpeechSynthesisVoice(language:)` when
> `AVSpeechSynthesisVoice(identifier:)` returns nil. Never force-unwrap a voice. **Prime the synthesizer at
> startup** with one silent utterance: the first real utterance otherwise pays a voice-asset load of
> hundreds of ms, which lands on the first hazard of the walk.
>
> **Cancellation.** `stopSpeaking(at: .immediate)` — never `.word`, which waits for a boundary.
> `withTaskCancellationHandler` around the delegate continuation, plus **two mandatory guards**:
> (i) a resume-once box, because `didFinish` and `didCancel` can both arrive and a double resume of a
> `CheckedContinuation` traps; (ii) an internal `SPEECH_WATCHDOG_MS = 1500` that resumes the continuation
> if no delegate callback ever arrives — `stopSpeaking` returns `false` and fires no delegate when the
> synthesizer is not actually speaking, which otherwise hangs `present` forever. Step 9's
> `PRESENT_TIMEOUT_MS = 2500` is a backstop, not the primary mechanism.
>
> **Haptic leads speech by `SPEECH_LEAD_MS = 120`.** Amend E5: the haptic starts at t=0, the utterance at
> t=120 ms. The body cue primes attention so the first phoneme is not missed — and the first phoneme is
> 100 % of the direction information in "Left side!". `present` duration is therefore
> `max(hapticMs, 120 + speechMs)`; E6's concurrency test must assert that, not `max` alone, and certainly
> not the sum. If on-device latency measurement makes 120 ms unaffordable, 0 is the documented fallback.

### Amendment 4 — Panning is route-aware, capped, cosmetic, and never applied to speech

*Why:* E1 specifies `pan ∈ [-1, 1]`; with one AirPod a hard pan is an 8–20 dB attenuator or, on a wired
bud, silence, so E as written can make a hazard announcement **inaudible** or make the spatial cue point
opposite the words.

Replace E1's `StereoPan.swift` line and add to E5:

> **Haptics carry direction unconditionally. The spoken sector words carry direction unconditionally.
> Panning is a bonus that must never be load-bearing and must never reduce audibility.** Three rules:
>
> 1. `StereoPan.pan(bearingDeg:) = clamp(sin(bearingDeg°), -1, 1) × PAN_CAP (0.4)`. Invariant asserted in
>    tests: `minChannelGain(pan) >= 0.6` (≈ −4.4 dB), so a listener with one bud in — on either side —
>    loses at most 4.4 dB of the panned component. `pan(0) == pan(180) == 0` (front/back collapse is
>    accepted; the haptic and the words separate them).
> 2. **Route gate.** Pan is non-zero only when `AVAudioSession.currentRoute.outputs` is a single port whose
>    type is in `{.headphones, .bluetoothA2DP, .bluetoothLE, .usbAudio, .airPlay}` **and**
>    `channels?.count ?? 1 >= 2`. `.builtInSpeaker` and `.builtInReceiver` force `pan = 0`: the speakers
>    are ~15 cm apart at 30 cm from the chest, and a phone mounted upside-down in the strap **swaps L and
>    R**, which would point the cue at the wrong side. Recompute on every
>    `AVAudioSession.routeChangeNotification`; never cache across a route change, because buds go in and
>    out mid-walk. iOS downmixes to mono for a single AirPod, but that is not something the route API lets
>    us verify, which is exactly why rule 1 exists.
> 3. **Pan is applied to a 40 ms generated earcon on an `AVAudioPlayerNode`, not to the utterance.**
>    `AVSpeechSynthesizer` can only be panned by routing `write(_:toBufferCallback:)` through
>    `AVAudioEngine`, which costs an `AVAudioConverter` (the callback yields int16 buffers), adds
>    pre-roll latency before the first phoneme and puts the 50 ms cancellation contract at risk — on a
>    hackathon clock, for a cue that carries no unique information. The earcon (1.2 kHz, 40 ms,
>    raised-cosine envelope, rendered into an `AVAudioPCMBuffer` in code — **mono**, because
>    `AVAudioMixing.pan` is a no-op on a multi-channel source — amplitude 0.5, played at t=0 alongside the
>    haptic) satisfies the runbook's "`AVAudioEngine` with L/R panning" literally, puts the whole panning
>    risk on an asset whose failure costs nothing, and does the one thing panning is actually good for:
>    an instant pre-alert. **This flags a tension with section A/B for the controller**: if the controller
>    reads "AVAudioEngine L/R stereo panning" as requiring *panned speech*, that is a P1 stretch behind a
>    flag with this earcon as the shipping default. `StereoPan.swift` ships pure and fully tested either
>    way; if time runs out, the earcon is the thing to cut, not the haptics.

### Amendment 5 — E4's exact session string, a degradation ladder, and the interruption / foreground rules

*Why:* E4 as written cannot activate before microphone permission exists, so a step-11 permission silences
step 10's speech; and E covers neither the phone call (which kills audio **and** the haptic engine) nor the
fact that CoreHaptics does not play when the app is not in the foreground — a chest-worn phone with the
screen off is a completely dead device.

Replace E4's second half with:

> The microphone ruling stands: **the iPhone's own microphone**, A2DP for output, **never `.allowBluetooth`
> (HFP)**. Exact configuration, once, in `AudioSessionConfig.configure()`:
>
> ```swift
> try session.setCategory(.playAndRecord,
>                         mode: .spokenAudio,
>                         policy: .default,
>                         options: [.duckOthers, .allowBluetoothA2DP, .allowAirPlay, .defaultToSpeaker])
> try session.setActive(true)
> ```
>
> **Degradation ladder (mandatory).** If either call throws — the common cause is that microphone
> permission has not been granted yet, but an exclusive session in another app does it too — retry **once**
> with `.playback` + `[.duckOthers, .allowBluetoothA2DP, .allowAirPlay]`, set `audioSessionDegraded`, log,
> and carry on. Speech is the safety channel; it must not be hostage to a permission that belongs to
> step 11. Step 11 re-attempts the `.playAndRecord` upgrade when it actually needs the microphone.
>
> **`.mixWithOthers` is not needed and must not be added.** `.duckOthers` already mixes rather than
> interrupts, so navigation-app audio, music and podcasts keep playing at reduced level. Adding
> `.mixWithOthers` alone (without ducking) would let a podcast mask a hazard word, which is a safety
> regression. `.interruptSpokenAudioAndMixWithOthers` is rejected: pausing the user's audiobook outright is
> a bigger intrusion than ducking it. **Document the consequence honestly:** while CaneOutput's session is
> active, the user's other audio is ducked for the whole walk, because we hold the session active
> continuously — activating per utterance would cost 20–100 ms and clip the first phoneme. That is the
> correct trade for a mobility aid and it is a disclosure, not a bug.
>
> **VoiceOver is unaffected by our category** and can speak over us; see §3.7 for the rules that keep that
> from becoming double speech.
>
> **Interruption (phone call, Siri, alarm) — currently an unwritten total failure.** Observe
> `AVAudioSession.interruptionNotification`. On `.began`: set `outputDegraded`, and because the CoreHaptics
> engine also receives `.audioSessionInterrupt`, **immediately attempt a haptic engine restart** — with
> `playsHapticsOnly = true` the haptic channel is the one that can survive, and urgency 3 must still fire
> during a call. On `.ended` with `shouldResume`: reactivate the session, restart the engine, clear the
> flag. The user must be told: speak "Output interrupted" if speech is available, otherwise play `.alarm`
> once on recovery.
>
> **Foreground and screen.** CoreHaptics does not play while the app is not in the foreground. A chest-worn
> phone whose screen has locked is silent *and* numb. Step 10 therefore requires of the app shell, and must
> assert at runtime: `UIApplication.shared.isIdleTimerDisabled = true` while a walk is active, the `audio`
> `UIBackgroundMode` declared so speech survives brief backgrounding, and a spoken warning if
> `UIApplication.shared.applicationState != .active` when a presentation is attempted. Also check
> `ProcessInfo.processInfo.isLowPowerModeEnabled` once at start and log it — it is the first thing to
> suspect when haptics feel weak.

### Amendment 6 — Anchor the bearing sign once; add the mirror and rounding guards; **no second throttle**

*Why:* a sign inversion passes every test in E6 and mirrors every announcement the product will ever make;
and a speech drop inside step 10 would silently lie to step 9's refractory bookkeeping.

Add to E1/E6:

> **One `Side`, one conversion point, one anchor test.** Following task-9 A2's precedent for
> `AngularMath`, `BearingSector.swift` is the only file that converts a bearing to a side, its header
> states the §C convention verbatim (`bearing = yaw_view + (c_x − 0.5) × FOV`; **0 is straight ahead and
> negative is the user's left**), and `HapticScript` and `StereoPan` both consume `Sector`/`Side` rather
> than re-deriving from the sign. Required tests, named so a reviewer cannot miss them:
> `test_negativeBearingIsTheUsersLeft` (asserting `sector(-90) == .left`, `sector(90) == .right`,
> `phrase(-90) contains "left"`, `pan(-90) < 0`), and a mirror property over `b in 1...179` asserting
> `sector(-b).side == sector(b).side.mirrored`. E6's boundary cases (14.9/15, 60, 120, 165, −165, 180) are
> kept and are necessary but not sufficient — they are all magnitude tests.
>
> **Rounding direction.** Step 9 owns the rounding into `.aboutMetres(Int)` (A12). Step 10 records the
> requirement and defends against violations: the rounding must be **floor**, never nearest-or-up, so the
> spoken distance is never further than the truth (4.9 m → "about 4 meters"), and step 10 renders `n < 1`
> as `"close"`. Filed as a step-9 recommendation in §4.
>
> **Step 10 adds no speech throttle and never drops or shortens a phrase it was handed — decided, not
> deferred.** Step 9 writes the `TrackRecord` and the `recentAnnouncements` entry at presentation *start*
> (A6, A9, A11). A step-10 drop would therefore leave step 9 believing the user had been warned about a
> hazard they never heard, and would suppress the *next* warning for that object via refractory: silent,
> undetectable, and exactly the wrong failure direction for a safety device. Two throttles in two layers
> also compose into silence nobody can explain. Step 10's contribution to anti-flooding is shorter phrases
> (Amendment 2) and a measurement: **log actual speech start and end times per presentation** so step 9's
> gap can be tuned against data. The gap-value recommendations are in §4.

### Amendment 7 — The blindfolded-listener protocol, made valid and runnable, plus E6 additions

*Why:* as written the protocol would produce a **false pass** — the Taptic Engine is audible, so an
unmasked listener discriminates the patterns by ear, and the test then measures hearing, not touch.

Replace E3's last bullet and extend E6:

> **Protocol (24 trials, ~10 minutes, one phone, one operator, one listener).**
>
> - **Masking is mandatory and is the reason the test is valid.** The listener wears closed headphones or
>   both AirPods with ANC playing pink noise at a comfortable level throughout. Without this the test is
>   invalid, because the Taptic Engine's own sound differs between patterns.
> - **Condition matches the product.** Standing and **marching in place at ~110 steps/min** (the cadence of
>   1.4 m/s) for every trial, phone in the real chest strap, in the demo orientation, **over the shirt the
>   demo will be done in**. Seated changes the coupling; still standing removes the gait transient that is
>   the whole confound.
> - **Onboarding, ~3 min, scripted:** read the Amendment 1 mnemonic sentence aloud, play each of the five
>   symbols twice in a fixed order with its name, then 4 unscored practice trials with feedback. This is
>   also the 2-minute-learnability measurement — record whether the listener needed more.
> - **Sequence:** a balanced multiset of 5 × `.ahead`, 5 × `.left`, 5 × `.right`, 5 × `.behind` plus
>   4 × `.alarm` catch trials, shuffled with a **printed seed**, constrained so no symbol repeats more than
>   twice consecutively. `HapticTrial(seed:)` generates and replays it, so a run is reproducible.
> - **Response:** 5-alternative forced choice — `ahead / left / right / behind / URGENT`. No "don't know",
>   no repeats, no feedback. Operator taps the answer; ~12 s per trial.
> - **Chance level:** 20 % (5-AFC). On the 20 directional trials alone, 4-AFC chance is 25 % (5 of 20
>   expected); binomially, 10/20 is already p ≈ 0.014, so "well above chance" is set with margin below.
> - **Pass requires all three:**
>   1. **≥ 16/20** directional trials correct (80 %; p < 10⁻⁷ against 25 % chance).
>   2. **Zero left↔right confusions.** This is the error that puts the user in the road. One occurrence
>      triggers Amendment 1's Plan B and a re-run; it is not averaged away.
>   3. **4/4 alarm trials called URGENT and zero directional trials called URGENT.** Urgency-3
>      recognition and the absence of false alarms are separate requirements from direction.
> - **Output:** `HapticTrial` writes a CSV (seed, trial index, presented symbol, response, correct,
>   response latency ms) into the app container for step 16 to export, prints the 5×5 confusion matrix, and
>   prints PASS/FAIL itself. The verdict is computed, not eyeballed.
> - `ios/CaneKit/Sources/CaneOutput/README.md` carries the operator script (≤ 25 lines, not 10 — the
>   masking and marching instructions are load-bearing) and the mnemonic sentence; the rationale stays in
>   this document.
>
> **Added to the E6 automated list:** the `HapticScript.validate()` alphabet invariants from Amendment 1;
> a `Phrasing` property test over the **full cross product** of 8 sectors × 12 classes × {closing nil, 1.0,
> 5.0} × {`.aboutMetres` 0…20, 3 bands, `.unknown`} asserting `wordCount <= 6`, non-empty, contains a §C
> sector word, no decimal anywhere, and never the substring "0 meters" — E2's "test the longest case"
> requires the implementer to guess which case is longest, and they will guess wrong; the urgency-3 word
> table over all eight sectors; `test_negativeBearingIsTheUsersLeft` and the mirror property; the pan
> invariants including `minChannelGain >= 0.6` and route-gate → `pan == 0`; `present` returns within 50 ms
> of `cancelCurrent()` with a fake renderer that only returns on cancellation; a speech renderer that never
> fires its delegate still lets `present` return (watchdog); `didFinish` + `didCancel` both firing does not
> double-resume; a second `present` while one is in flight stops the first haptic player (no
> superposition); `supportsHaptics == false` ⇒ one spoken degradation notice and speech still delivered for
> every urgency including 1; total presentation time ≈ `max(hapticMs, 120 + speechMs)`.

## 3. Answers to the eight questions

**Q1 — Haptic vocabulary: is long-short vs short-long learnable and distinguishable?**
**No — verdict: reject E3's rhythm code; ship the five-symbol alphabet in Amendment 1.** Long-short and
short-long are exact temporal mirrors, which is the dominant confusion class for single-actuator patterns
because it requires holding element 1 in memory and comparing it to element 2; a shirt-attenuated or
gait-masked first element converts left into right. E3 is also over-subscribed at nine symbols, and its
`ahead` (2 × 120 ms) vs `left` (200 + 80 ms) differ by a 1.67:1 duration ratio, near the torso Weber limit.
The replacement is two texture families (clicks = lateral, buzzes = front/back) with a cardinality
difference rather than a mirror for left vs right, learnable from one sentence with an ordinal mnemonic
(L before R, two before three). Concrete set, gains, and the urgency-3 rule are in Amendment 1; the rule
that keeps urgency 3 unmistakable is **continuity**: nothing else in the alphabet lasts more than 420 ms or
exceeds intensity 0.85, so "still going" ⇒ "act now", with no counting or comparison, enforced as a unit
test rather than as prose.

**Q2 — Speech: length, order, rate, and the urgency-3 word set.**
**Wrong length, wrong urgency-3 words, right instinct about brevity — verdict: amend both.** E2's worst
case is nine words (~1.9 s at default rate) against a 1.2 s budget. Fix by cutting words rather than racing
the clock: drop "closing" entirely (replace with "fast" only above step 9's `FAST_CLOSING_MPS`), speak
range only for statics straight ahead where distance actually changes the action, and cap at 6 words with a
deterministic drop order. `rate = 0.55` (modestly above the 0.5 default; user-adjustable 0.45–0.70 — faster
is a real blind-user preference, but one open ear in traffic and a first-time listener bound how far), pitch
1.00 at urgency 2 and 1.15 at urgency 3 as a redundant urgency cue with better penetration through
low-frequency traffic noise, `preUtteranceDelay = postUtteranceDelay = 0`. **Direction before class**
("behind you, cyclist"), for four reasons, the decisive one being that urgency 3 preempts urgency 2 by
design, so every phrase can be truncated mid-word — direction-first guarantees the surviving fragment is
the actionable one, and it makes urgency 2 and 3 one vocabulary. **"Stop / Left! / Right! / Behind!" is not
the right set**: "Left!" does not distinguish "hazard on your left" from "move left", and the system cannot
model free space, so it must never issue a steering command. Use "Stop!" / "Left side!" / "Right side!" /
"Behind you!" — ≤ 2 words, ~450 ms, unambiguously locative.

**Q3 — Stereo panning with one AirPod.**
**Verdict: haptics and words carry direction unconditionally; panning is route-gated, capped, cosmetic, and
never applied to speech.** The decisive point is not that pan is useless with one ear — it is that pan is a
*gain* control, so E's ±1 pan can make a hazard announcement 8–20 dB down, or silent on a wired bud, on the
side the user is not wearing. That converts a presentation nicety into a total-failure mode. Detect the
route (and recompute on every route change — buds go in and out mid-walk) and force `pan = 0` on speaker,
receiver and any non-stereo route; cap `|pan| ≤ 0.4` so no listener loses more than ~4.4 dB; put the pan on
a 40 ms mono earcon rather than on the utterance. One AirPod cannot be detected through the route API,
which is precisely why the cap, not the detection, is the load-bearing mitigation. Detail in Amendment 4.

**Q4 — Audio session.**
**Verdict: keep `.playAndRecord`/`.spokenAudio`/`[.duckOthers, .allowBluetoothA2DP, .allowAirPlay,
.defaultToSpeaker]`, add a `.playback` fallback and interruption handling; `.mixWithOthers` is not needed
and must not be added.** Behaviour: **VoiceOver** is unaffected by our category and will happily speak over
us (the fix is §3.7's rules, not a session option). A **phone call** interrupts the session *and* stops the
CoreHaptics engine — the unwritten worst case in E, since the cane goes both mute and numb with no notice;
handled by the interruption observer plus `playsHapticsOnly` so the haptic channel is the one that can be
brought back. **Music, podcasts and navigation audio** are ducked (not interrupted) for as long as our
session is active, which is the whole walk, because per-utterance activation would clip the first phoneme —
an honest disclosure rather than a bug, and the right trade for a mobility aid; `.mixWithOthers` without
ducking would let a podcast mask a hazard word. The **AirPod route** is A2DP out with the built-in mic in,
which is exactly what omitting `.allowBluetooth` buys — E4's reasoning here is correct and ratified. The
one thing E4 gets wrong is failure: `.playAndRecord` cannot be activated before microphone permission
exists, so without the `.playback` retry a step-11 permission prompt silences step 10 entirely. Exact
strings in Amendment 5.

**Q5 — Flooding and masking.**
**Verdict: no. Step 10 adds no second throttle and never drops or shortens an announcement it was handed.**
The architectural reason overrides the perceptual one: step 9 writes the `TrackRecord` and
`recentAnnouncements` entry at presentation *start*, so a step-10 drop would make step 9 believe the user
was warned and would then suppress the next warning for that object via refractory — a silent failure that
degrades future safety, not just the current announcement. Two throttles in two layers also produce silence
nobody can explain. That said, **1200 ms is not enough** on the merits: with ~1.0 s of speech per 1.2 s gap
the duty cycle approaches 45 %, and the sounds this user needs — footsteps behind, engine note, a door, and
above all the acoustic return of their own cane tap, which is a primary perceptual channel — are being
masked half the time in the only ear that is open. The fix belongs upstream and is filed in §4:
`MIN_SPEECH_GAP_MS` 1200 → **1800**, measured from speech **end** (stamp `lastSpeechAtMs` when `present`
returns, not when it starts). Step 10's own contribution is real and sufficient: phrases cut from nine
words to six, "closing" removed, range spoken only where it changes the action, and per-presentation
speech start/end logging so the gap can be tuned against measurement rather than argument.

**Q6 — The blindfolded-listener protocol.**
**Verdict: E3's protocol would produce a false pass; the corrected 24-trial design is Amendment 7.** The
critical omission is acoustic masking — the Taptic Engine is audible and its sound differs by pattern, so
an unmasked listener passes by ear. Also required: marching in place at demo cadence (gait transients are
the confound), the real strap over the real shirt, a seeded balanced sequence, 5-AFC with no "don't know",
4 interleaved alarm catch trials, and a computed verdict. Chance is 20 % over all 24 trials and 25 % over
the 20 directional ones; the bar is **≥ 16/20 directional (p < 10⁻⁷), zero left↔right confusions, and 4/4
alarms with zero false alarms** — three separate criteria, because an 85 % score built on two left/right
swaps is a fail, not a pass. Total ~10 minutes including a 3-minute scripted onboarding that doubles as the
2-minute-learnability measurement.

**Q7 — WCAG / iOS accessibility hooks for a non-visual, voice + haptic app.**
**Verdict: four hard requirements, one explicit anti-requirement, and a clear scope line.** *Must respect:*
(i) **never** route hazard speech through `UIAccessibility.post(notification: .announcement, ...)` — our
synthesizer is the channel; using both gives double speech with unpredictable ordering. There is no public
API to pause VoiceOver and none should be sought; instead, when `UIAccessibility.isVoiceOverRunning` is
true the walking screen must be **inert** — no live regions, no self-announcing status changes, one
element, `accessibilityViewIsModal`, observed via `voiceOverStatusDidChangeNotification`.
(ii) Implement **`accessibilityPerformMagicTap()`** (two-finger double-tap) as start/stop — the native
iOS "primary action without looking" affordance, and the answer to WCAG 1.4.2 Audio Control and 2.2.2
Pause/Stop for a continuous announcement stream. (iii) **1.3.3 Sensory Characteristics / 1.1.1 Non-text
Content**: direction must exist in two channels (haptic **and** words) — this is the criterion E's hard pan
would have violated, and it is why Amendment 4 demotes panning; the haptic alphabet is non-text content
conveying meaning, so the `HapticReference` self-test is its required text equivalent. (iv) **2.2.1 Timing
Adjustable / 3.3.7 Redundant Entry**: speech rate and haptic gain must be user-adjustable **and
persisted** — never re-calibrate on every launch. *Anti-requirement:* `isReduceMotionEnabled` is about
visual motion and **must not** gate haptics; suppressing the primary channel of a mobility aid because of
an animation preference would be a serious defect. There is no public "haptics disabled" query, so the
correct pattern is the pre-walk self-test plus `supportsHaptics`/engine-start detection plus **audible**
degradation. Foreground/idle-timer/background-audio is a hard runtime requirement (Amendment 5), not a
nicety. *Out of scope for step 10:* contrast ratios, Dynamic Type, focus order, target size and VoiceOver
labels for the visible shell (steps 13/14 — the shell still owes 44×44 targets, 4.5:1 text and Dynamic Type
up to accessibility sizes for sighted helpers and the demo jury); Guided Access is a deployment
recommendation for the demo (it prevents an accidental swipe out of the app, which would kill haptics) and
needs no code; localization.

**Q8 — Anything in E that could cause a wrong action.**
**Verdict: six, of which three are first-order.** First order: (i) **"Left!" / "Right!"** read as a steering
command → the user steps **into** the hazard (Amendment 2); (ii) **`behind` = 3 × 80 ms vs `.burst` =
3 × 80 ms** — identical numerosity and element duration, separated only by the two parameters that vary
with strap tension and clothing, so "be aware" can read as "STOP" and, far worse, "STOP" can read as "be
aware" (Amendment 1); (iii) an **unanchored bearing sign** — nothing in E6 asserts that negative is left, so
a sign inversion passes the whole suite and mirrors every announcement the product ever makes
(Amendment 6). Second order: (iv) **hard pan vs words disagreeing** — a spatial cue is acted on faster than
a word, and a phone mounted upside-down in the strap swaps the built-in speakers, so the pan can point at
the wrong side (Amendment 4); (v) **urgency 1 at intensity 0.5 through a shirt may be sub-threshold**, and
because step 9 stamps the `TrackRecord` at presentation start, an unfelt urgency-1 haptic then suppresses
that object for `refractory(1) = 8000` ms — a cue the user never received actively silencing the next eight
seconds (Amendment 1's intensity floor, calibration and self-test); (vi) **`.aboutMetres(0)` → "about 0
meters"**, which reads as impact, plus E2 leaving the rounding *direction* unstated so "about 5 meters"
could be spoken for 4.6 m — the error must always be conservative (Amendments 2 and 6). One more worth
recording though it needs no amendment: urgency 3 preempting urgency 2 mid-phrase can leave the user having
heard "to your left, cy—" immediately followed by "Stop!"; direction-first ordering plus
`stopSpeaking(at: .immediate)` plus the 120 ms haptic lead make the last cue received the correct one, which
is the best available outcome and is the reason the ordering decision in Amendment 2 is a safety decision
rather than a style one.

## 4. Hazards for later steps — recorded, **not** to be acted on by the step-10 implementer

- **R1 (step 9 knob, needs a controller ruling — recommendation, not an amendment).**
  `MIN_SPEECH_GAP_MS` 1200 → **1800**, and stamp `lastSpeechAtMs` when `present` **returns** rather than
  when it starts, so the gap is measured from speech end. A11 does not say which, and the difference is a
  ~45 % vs ~36 % speech duty cycle in the user's one open ear. H6 already lists `MIN_SPEECH_GAP_MS` as a
  demo knob, so this is a knob turn, not a redesign. The step-10 implementer changes nothing in step 9.
- **R2 (step 9, `.aboutMetres` rounding direction).** A12 says "rounded whole metres" without a direction.
  It must be **floor**, so the spoken distance is never further than the truth. Step 10 defends
  independently by rendering `n < 1` as "close".
- **R3 (step 9, urgency-3 repetition).** With `URGENCY3_REPEAT_MS = 1000` and a 900 ms alarm, a persisting
  hazard produces a near-continuous haptic roar — which is correct and desirable — but also repeats
  "Stop!" every second, which is a speech flood. Consider speaking the urgency-3 word only on the first
  presentation for a given `(source, trackId)` and letting repeats be haptic-only. This requires
  repeat-count knowledge that only step 9 has; step 10 must **not** implement it locally, for the same
  reason it adds no throttle (Amendment 6).
- **H-11a (step 11, voice input).** Do **not** set mode `.voiceChat`: it implies HFP, which drops AirPods
  output to call quality and re-routes TTS — the exact failure E4 avoided. Use `.spokenAudio` or
  `.default` with the built-in mic. Step 11 owns the `.playAndRecord` upgrade (and the microphone
  permission prompt) and must call it while the walk is paused, never mid-announcement, and must restore
  step 10's configuration afterwards. It must also expect the `.playback` degraded state from Amendment 5.
- **H-11b (step 11).** Voice input will capture our own TTS through the iPhone mic. Either gate recognition
  while `SpeechRenderer` is speaking, or accept echo; without AEC (which we cannot have, having rejected
  `.voiceChat`) the app will otherwise hear itself say "cyclist".
- **H-12a (step 12, navigation cues — the important one).** `HapticScript` is reusable by step 12 as E7
  requires, but step 12 **must draw its turn cues from a disjoint region of the same design space** and
  must pass `HapticScript.validate()`. Specifically: no step-12 script may last more than 420 ms, exceed
  intensity 0.85, or contain a continuous element longer than 400 ms, because those properties are what
  make urgency 3 unmistakable. A turn cue that feels like the alarm is a wrong-action defect in the most
  literal sense. Reusing `.left` / `.right` for "turn left/right" is acceptable **only** with a distinct
  leading accent or a distinct gain tier, and only after re-running the Amendment 7 protocol with the
  combined alphabet — five symbols passed; nine may not.
- **H-12b (step 12).** Navigation speech and hazard speech share one synthesizer and one 1800 ms gap.
  Hazard speech must win; step 12 needs an explicit priority below urgency 1, and its phrases must obey
  the same direction-first word order (WCAG 3.2.4) or the user will be learning two vocabularies.
- **H-13/14 (app shell).** Step 10 depends on the shell for `isIdleTimerDisabled`, the `audio` background
  mode, and an inert VoiceOver-safe walking screen with a magic-tap stop. If the shell does not provide
  them, a chest-worn phone with a locked screen is a completely dead device and the step-10 tests will all
  still pass.
- **H-16 (metrics).** Two new exports beyond E5's latency number: the `HapticTrial` CSV (seed, confusion
  matrix, verdict) as the recorded evidence for the runbook's acceptance criterion, and the per-presentation
  speech start/end timestamps that R1 needs in order to be tuned from data.
- **Unresolved, for the controller.** Amendment 4 narrows "AVAudioEngine L/R stereo panning" (sections
  A and B, verbatim spec) to a panned earcon rather than panned speech. If the controller reads the spec as
  requiring panned speech, that becomes a P1 stretch behind a flag, with the earcon as the shipping
  default; `StereoPan.swift` ships pure and tested under either ruling, and the pan cap and route gate hold
  in both cases because they are audibility requirements, not implementation choices.
