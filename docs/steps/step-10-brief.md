# Task 10 brief — Haptics + TTS + stereo panning output

Extracted from docs/orchestration-runbook.md (Step 10), docs/build-plan.md F5 + §6 conflict #1, docs/insta360feed.md bearing words, plus controller rulings and the task-9 architect's hazards H2/H3. Exact values are binding.

## A. Runbook step text (verbatim)

## Step 10 — Haptics + TTS + stereo panning output

**Intent:** The only channel the user actually experiences.
**Chain rationale:** `a11y-architect` leads because this *is* the accessibility surface — for a
blind user the haptic vocabulary is the interface, not a presentation detail.

```bash
/everything-claude-code:orchestrate custom "ecc:a11y-architect,ecc:tdd-guide,ecc:swift-reviewer" "[Plan: docs/device-responsibilities.md#step-10] Build the output layer: CoreHaptics directional patterns keyed to bearing, AVSpeechSynthesizer TTS, and AVAudioEngine L/R stereo panning, since AVAudioEnvironmentNode is cut. Announcements round to \"about two metres\", never \"2.3 metres\", and urgency thresholds use bands with hysteresis so a noisy estimate cannot flip urgency back and forth. Acceptance: a blindfolded listener identifies left, right and ahead from haptics alone well above chance; urgency 3 preempts an in-progress announcement; urgency 1 never speaks"
```

---


## B. Spec: build-plan.md F5 and conflict #1 (verbatim)

### F5 · Haptic / audio output layer — MVP, P3

- **Hardware** — iPhone Taptic Engine · speakers or AirPods (A2DP out)
- **Libraries** — `CoreHaptics` (`CHHapticEngine`, `CHHapticPatternPlayer`) ·
  `AVSpeechSynthesizer` · `AVAudioEngine` with L/R panning
- **Computation** — bearing → directional haptic pattern; bearing → stereo pan position;
  announcement text rounds to **"about two metres," never "2.3 metres"**; urgency thresholds use
  bands with hysteresis so a noisy estimate cannot flip urgency back and forth
- **Cut** — `AVAudioEnvironmentNode` true spatial audio → **stereo panning** instead (hours of
  work for marginal demo gain)
- **Cloud / external** — none

---

**Conflict #1 — which microphone?** `device-responsibilities.md` says "Microphone (**iPhone's, not
AirPods'**)". `camera-sync-design.md` §5 says "the AirPods mic is fine for voice input." These
contradict. The `CMHeadphoneMotionManager` reason for avoiding the AirPods mic is gone (head
tracking is cut), but `.allowBluetooth` still changes the audio session category, which affects TTS
routing. **Pick one and write it down.**

> **Resolved 2026-09-26 — the iPhone's own microphone.** Recorded in `build-plan.md` §6
> conflict #1; `camera-sync-design.md` §5 is annotated as superseded. The binding session
> configuration is Amendment 5 of `step-10-design.md`: never `.allowBluetooth`, so output
> stays on A2DP and `.defaultToSpeaker` covers the no-AirPods case.


## C. Spec: insta360feed.md bearing words (verbatim)

**Where is it?** Each event gets a bearing from its box centre inside its view:

```
bearing = yaw_view + (c_x - 0.5) * FOV, wrapped to [-180, 180)
```

`c_x` is the box centre as a fraction of the view width; 0 means straight ahead and negative means left. `position` is the coarse third of the view the centre falls in (left, center, right). Speech uses the bearing:

| Bearing magnitude | Words |
| --- | --- |
| under 15° | straight ahead |
| 15° to 60° | ahead to the left / right |
| 60° to 120° | to your left / right |
| 120° to 165° | behind you to the left / right |
| over 165° | directly behind you |


## D. What step 9 delivers (the seam this step implements)
From `task-9-design.md` A12/A13 (read them): `Announcement { id, urgency, cls, bearingDeg, range: RangeHint (.aboutMetres(Int) | .band(RangeBand) | .unknown), closingMps, haptic: HapticKind (.burst | .directional), speech: SpeechVerbosity? (.oneWord | .brief | nil), source, trackId, tCaptureMs, decidedAtMs, expiresAtMs }` and
```swift
public protocol AnnouncementSink: Sendable {
  func present(_ a: Announcement) async     // returns when finished or cancelled; must not throw
  func cancelCurrent()                      // sync, non-blocking, idempotent, safe when idle
}
```
Hazards for this step: **H2** — the urgency-3 word depends on the bearing sector ("Stop" is wrong for an overtaking cyclist at 178°). **H3** — `present` must be fast and cancellable: speech ≤ 1.2 s, return within ~50 ms of `cancelCurrent()`, `withTaskCancellationHandler` around the AVSpeech delegate continuation; step 9 hard-times-out at 2500 ms.

## E. Controller rulings — binding (the a11y-architect may amend; amendments land in task-10-design.md)

### E1. Placement
New target `CaneOutput` in `ios/CaneKit` (depends on `CaneContracts`, `CaneFeedback`). Pure policy files are Foundation-only and unit-tested on macOS; engine wrappers are behind protocols with `#if canImport(...)` guards so the package still builds and tests on macOS.
```
Sources/CaneOutput/
  Phrasing.swift          # pure: Announcement -> SpokenPhrase (String) — rounding, sector words, class words, urgency-3 word table
  BearingSector.swift     # pure: bearingDeg -> Sector (.ahead, .aheadLeft, .aheadRight, .left, .right, .behindLeft, .behindRight, .behind) with the §C thresholds
  HapticScript.swift      # pure: (HapticKind, bearingDeg, urgency) -> HapticScript (a value: list of timed events with intensity/sharpness and a side)
  StereoPan.swift         # pure: bearingDeg -> pan in [-1, 1] (sin-based, clamped; rear bearings pan to the side they are on)
  HapticRenderer.swift    # protocol HapticRenderer { func play(_ script: HapticScript) async; func stop() } + CoreHapticsRenderer (#if canImport(CoreHaptics))
  SpeechRenderer.swift    # protocol SpeechRenderer { func speak(_ phrase: SpokenPhrase, pan: Double) async; func stop() } + AVSpeechRenderer (#if canImport(AVFoundation))
  AudioSessionConfig.swift# configures AVAudioSession (see E4) — iOS only, no-op elsewhere
  OutputSink.swift        # OutputSink: AnnouncementSink — composes haptic + speech concurrently, cancellation, 50 ms return budget
Tests/CaneOutputTests/    # PhrasingTests, BearingSectorTests, HapticScriptTests, StereoPanTests, OutputSinkTests (fake renderers)
```

### E2. Phrasing rules (pure, tested)
- Range: `.aboutMetres(n)` → `"about \(n) meters"` (digits are fine for TTS; 1 → "about 1 meter"); `.band(.near)` → `"close"`, `.band(.mid)` → `"nearby"`, `.band(.far)` → `"far"`; `.unknown` → nothing. Never render a decimal.
- Sector words from §C: |b| < 15 "straight ahead"; 15–60 "ahead to the left/right"; 60–120 "to your left/right"; 120–165 "behind you to the left/right"; ≥ 165 "directly behind you".
- Class words: person, cyclist, scooter, car, bus, pole, bollard, wall, "parked car", door, overhang, unknown_obstacle → "obstacle".
- Urgency 2 (`.brief`): `"\(class) \(sector)[, \(range)][, closing]"` where "closing" is appended iff `closingMps > 0`. Target ≤ 6 words; test the longest case.
- Urgency 3 (`.oneWord`) word table over the sector (H2): ahead/aheadLeft/aheadRight → "Stop"; left → "Left!"; right → "Right!"; behindLeft/behindRight/behind → "Behind!". One word, exclamation optional.
- Urgency 1: `speech == nil` → no phrase; `Phrasing.phrase(for:)` returns `nil` and the sink never calls the speech renderer.

### E3. Haptic vocabulary (a11y-architect to confirm or amend)
- `.burst` (urgency 3): 3 × 80 ms transients at intensity 1.0, sharpness 1.0, 60 ms gaps, no directional component.
- `.directional` (urgency 2): 2 × 120 ms continuous pulses, intensity 0.8, at `pan` derived from bearing — since the Taptic Engine is mono, direction is encoded in **pattern**, not position: left = long-short (200 ms + 80 ms), right = short-long (80 ms + 200 ms), ahead = two equal 120 ms, behind = three equal 80 ms. Urgency 1 uses the same directional pattern at intensity 0.5.
- `HapticScript` is a value type; `CoreHapticsRenderer` converts it to `CHHapticPattern` events. A cancelled script stops within 50 ms (`CHHapticPatternPlayer.stop(atTime:)`).
- The acceptance test "a blindfolded listener identifies left, right and ahead from haptics alone well above chance" is a human test: document a 20-trial protocol in `ios/CaneKit/Sources/CaneOutput/README.md` (10 lines) and ship a tiny `HapticTrial` helper that plays a random one of the three and records the listener's answer.

### E4. Audio session and the microphone decision (build-plan §6 conflict #1)
Ruling: **the iPhone's own microphone is the voice input** (device-responsibilities.md is the newer, more specific doc). Therefore the audio session is `.playAndRecord` with options `[.duckOthers, .allowBluetoothA2DP, .defaultToSpeaker]` and **without** `.allowBluetooth` (HFP) — HFP would drop AirPods output to phone-call quality and re-route TTS. Mode `.spokenAudio`. Configure once in `AudioSessionConfig.configure()`; step 11 reuses it and must not reconfigure the category.

### E5. OutputSink behaviour (tested with fake renderers)
- `present(a)`: compute phrase + script + pan; start haptic and speech concurrently (`async let`); await both; return. If `a.speech == nil` never touch the speech renderer.
- `cancelCurrent()`: synchronous, idempotent; calls `stop()` on both renderers and cancels the in-flight task; `present` must return within 50 ms of the call (test with a fake renderer that only returns on cancellation).
- If `now >= a.expiresAtMs` when `present` is called, present nothing and return immediately (belt-and-braces; step 9 already re-gates).
- Log each presentation with `id`, urgency, phrase, and `decidedAtMs − tCaptureMs` (this is the §4.9 latency number; step 16 exports it).

### E6. Tests (TDD)
Phrasing (every rule in E2 including the ≤ 6-word check and "urgency 1 → nil"); BearingSector thresholds at the boundaries (14.9/15, 60, 120, 165, −165, 180); HapticScript patterns per sector and urgency; StereoPan (0 → 0, 90 → 1, −90 → −1, 180 → 0 or side-consistent, clamped); OutputSink: urgency 1 never calls speech; urgency 3 calls haptic burst + one word; cancellation returns within 50 ms; expired announcement presents nothing; the two renderers run concurrently (total time ≈ max, not sum, using fake renderers with known delays).

### E7. Out of scope
Voice input (11); navigation cues (12) — but `HapticScript` must be reusable by 12 for turn cues; `AVAudioEnvironmentNode` (cut).

## F. Global constraints (verbatim)
- "Announcements round to 'about two metres', never '2.3 metres'"; "urgency 3 preempts an in-progress announcement; urgency 1 never speaks".
- "for a blind user the haptic vocabulary is the interface, not a presentation detail."
- Commit format `<type>: <description>`; no attribution trailers. Immutable values; small files; explicit error handling.

## G. Accessibility-architect amendments — ACCEPTED and BINDING
Read `task-10-design.md` (same directory) §2 amendments 1–7 in full before writing any code. They are binding; where they conflict with section E above, the amendment wins. In particular: the 5-symbol haptic alphabet (2 wide clicks = left, 3 quick clicks = right, short bump = ahead, long swell = behind, 900 ms rattle = alarm) with gain floor, one-player rule and audible degradation; phrasing = direction before class, ≤ 6 words with a drop order, no "closing", urgency-3 words "Stop! / Left side! / Right side! / Behind you!"; AVSpeechUtterance rate 0.55, pitch 1.15 at urgency 3, zero delays, pinned voice + priming, resume-once guard + 1500 ms watchdog, haptic leads speech by 120 ms; panning route-gated, |pan| ≤ 0.4, applied to a 40 ms earcon, never to speech; E4's session kept plus `.playback` fallback, interruption handling, foreground/idle-timer rules; bearing sign anchored in one file with a named test; NO second speech throttle in step 10; the 24-trial pink-noise-masked acceptance protocol with computed verdict + CSV, plus the added automated tests listed there.

## Controller decision (2026-09-26) — navigation cues vs hazard speech
Hazard announcements always win. A navigation `TurnCue` of kind `prepare`, `now`, `correctHeading` or `continueStraight` that arrives while a hazard announcement is presenting, or inside the 1800 ms speech gap after it, is dropped (never queued); `arrived`, `offRoute`, `gpsLost` and `gpsRestored` are re-issued on the next tick after the gap. Hazard haptics preempt navigation haptics. See docs/orchestration-runbook.md "Demo decisions" and step-12-brief.md F4.
