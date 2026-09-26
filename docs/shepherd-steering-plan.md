# Shepherd's sensing & steering pipeline, ported to the X4 + phone

Source: [tonywangs/shepherd](https://github.com/tonywangs/shepherd#sensing--steering-pipeline).
Their nine-stage chain is the reference design; this plan keeps the **structure and the maths** and
substitutes our hardware. Steps continue the `docs/orchestration-runbook.md` numbering (1–20 taken),
so the new work is **21–26**. Every step below is additive: nothing already on `main` changes
behaviour except where §4 says so explicitly.

## 0. What Shepherd does, and what we do instead

| # | Shepherd stage | Their means | Ours | Status today |
|---|---|---|---|---|
| 1 | Depth capture | ARKit LiDAR depth, 30–60 Hz, one ~60° front view | Depth Anything V2 Small on the **three forward X4 crops** (C5/C0/C1 = −90…+90°), at the bridge's rung fps (~3 Hz measured) | benchmark only (`cane.benchmarks.depth`, 53 ms p50 on MPS) — **not in the pipeline** |
| 2 | Obstacle detection | depth map in left/centre/right zones, vertical filtering to ignore ceiling and floor | same idea, class-agnostic, on the forward crops; vertical band filter from crop pinhole geometry | **missing** — we only see the 12-class YOLO enum, so a wall or hedge is invisible |
| 3 | Person detection | Vision `VNDetectHumanRectanglesRequest` at ~2 Hz | YOLO11s `person` + ByteTrack + closing speed at ~3 Hz | **done** (steps 6–8), reused unchanged |
| 4 | Gap profiling | 16 vertical columns, mean depth per column, smoothed `[0.25, 0.5, 0.25]` | **48 columns across 180°** (16 per forward crop), same kernel, smoothed *across* crop seams | **missing** |
| 5 | Steering computation | `command = sqrt(abs(gapDirection)) * proximityFactor`, `proximityFactor` ramps 0 (clear) → 1 (obstacle < 0.2 m) | same law, same ramp shape; `proximityFactor` anchored by ground-plane geometry (§2.3) instead of LiDAR metres | **missing** |
| 6 | Navigation merge | GPS bias added, scaled by `(1 - proximityFactor)` so obstacles win | same blend, computed on the phone inside `CaneFeedback`/`CaneOutput` | **partly** — today nav cues are *dropped* under hazard, never blended (runbook "Demo decisions" 2) |
| 7 | BLE transmission | 12-byte `{speed, angle, distance, mode}` at 10–20 Hz to an ESP32 | new `steer` frame on the **existing WebSocket**, at rung fps | **missing** (contract amendment, step 21) |
| 8 | Motor response | leaky integrator, boat-like momentum, PWM to a motor | leaky integrator driving a **continuous stereo-panned earcon + haptic pulse rate**; we have no motor, the wearer is the actuator | **missing** — `CaneOutput` has `StereoPan`, `HapticScript`, `EarconPlayer` to build on |
| 9 | Safety failsafe | BLE drop → motor power decays to 0 over ~500 ms | `steer` TTL 400 ms → phone **decays the cue to neutral over 500 ms**; bridge in local-fallback mode stops sending rather than faking | **missing** (the rung/health half exists, step 8) |

Their voice layer is Vapi. Ours is already built and is **not part of this plan**: wake word →
`IntentParser` → `.navigate(place)` → `NavigationEngine` (Routes v2) for directions, and Gemini
(`cane.scene`, `gemini-3.5-flash-lite`) for `find` / `describe_around` / `describe_front`. Step 26 is
the one small addition that lets the voice layer see the new signal.

## 1. Three consequences of the hardware swap

1. **No LiDAR, so no metres.** Depth Anything V2 returns *relative* inverse depth. Their `0.2 m`
   threshold cannot be applied directly. §2.3 anchors it geometrically. **Per-frame min-max
   normalisation must never feed `proximityFactor`** — it would make an empty corridor look exactly
   as dangerous as a wall, because the nearest thing in frame always normalises to 1.0. Gap
   *direction* may use relative depth freely (it is a ranking); gap *proximity* may not.
2. **360° instead of 60°.** They steer from the front view only. We have the whole ring, but depth on
   six crops costs twice what steering needs, and you steer where you walk — so depth runs on the
   **three forward crops** and the rear three stay on the existing YOLO approach-warning path.
3. **No motor, and a 3 Hz sensing rate against their 10–20 Hz.** The cue must be re-rendered on the
   phone faster than frames arrive; the leaky integrator (their stage 8) is what makes 3 Hz of
   evidence feel continuous, and the §9 decay is what stops it lying when frames stop.

**A note on the phone's camera, because it reads backwards.** The phone is chest-mounted with its
*screen facing outward* (the "reactor" display, step 20), so the **front/selfie camera is the one
looking where the wearer looks** and the rear cameras point at the wearer's chest. This is already
how `CaneCamera.FrontCamera` is built — `position: .front`, mirroring off, Center Stage off,
stabilization off — and `CameraGeometry` documents why mirroring must stay off: an unmirrored
front-camera frame puts image-left on the wearer's left, so every bearing keeps its sign. Verified in
code 2026-09-26; no change needed. The consequence for this plan is that the phone's forward view is
the selfie sensor's ~70° at 1280×720, narrower and softer than the rear camera it is *not* using —
one more reason depth lives on the X4 crops (§1.2) and not on the phone.

## 2. Where each piece runs, and why

The split mirrors the one the detection path already proved: **the cloud emits timeless facts, the
bridge owns geometry and the clock, the phone owns actuation.**

### 2.1 Cloud (`backend/src/cane/cloud`) — step 22
Adds a depth head to the existing `POST /infer`. For each forward crop it returns **16 column
values** of the raw relative depth, already vertically band-filtered, plus the ground-row fit
residual. It stays timeless: it echoes `t_capture_ms`, emits no bearings, no metres, no steering.

### 2.2 Bridge (`backend/src/cane/bridge`) — step 23
Owns what it already owns for detections: crop yaws (`CROP_YAWS_DEG`), the mount offset `delta_deg`,
the phone clock, the rungs and the fallback. It assembles the three 16-column blocks into one
48-column ring over −90…+90°, smooths across the seams, computes `gapDirection` and
`proximityFactor`, applies the steering law, and sends one `steer` frame per tick.

### 2.3 Anchoring `proximityFactor` without LiDAR
The X4 sits on the shoulder at a known height `h` (≈1.40 m, measured once at build) and the crops are
rectilinear with a known focal length — `dewarp.focal_px(crop_px)` already exists. A ground point at
horizontal distance `d` lands `dRow = focal_px * h / d` pixels below the horizon, so the ground rows
give a one-parameter fit from relative depth to metres. Two guards:

- the fit is rejected when its residual is high (stairs, a slope, a crowd covering the floor); on
  rejection the bridge emits `proximity_anchored = false` and the phone treats proximity as a
  **band**, not a number — the same "trust the band, not the metre" rule `RangeTrust.swift` already
  applies to phone ranges.
- the fit is cross-checked against step 7's pinhole `range_band` for any detection in the same crop.
  Disagreement by more than one band invalidates the frame's anchor.

The ramp then keeps Shepherd's shape: `proximityFactor = clamp((d_clear - d_min) / (d_clear - d_stop), 0, 1)`
with `d_stop = 0.6 m` and `d_clear = 3.0 m`. Their `0.2 m` is a motor-stopping distance for a wheeled
cane tip; a walking wearer needs to be told earlier, and 0.6 m is roughly one cane sweep.

### 2.4 Phone (`ios/CaneKit/Sources/Cane{Feedback,Output}`) — step 24
Their stages 6, 8 and 9. Leaky integrator, navigation blend, continuous cue, decay to neutral.

## 3. Constants (one table, no magic numbers in code)

| name | value | origin |
|---|---|---|
| `DEPTH_CROPS` | crop indices 5, 0, 1 — yaw **centres** −60, 0, +60, each 60° FOV, so together they **cover** −90…+90° | §1.2 |
| `COLUMNS_PER_CROP` | 16 | Shepherd stage 4 |
| `RING_COLUMNS` | 48 (3.75° each, spanning −90…+90°) | 3 crops × 16 |
| `SMOOTH_KERNEL` | `[0.25, 0.5, 0.25]` | Shepherd stage 4, verbatim |
| `MOUNT_HEIGHT_M` | 1.40 | measured at build, per wearer |
| `BODY_WIDTH_M` | 0.70 | a gap narrower than this is not a gap |
| `OBSTACLE_BAND_M` | 0.20 … 2.20 above ground | Shepherd's "ignore ceiling/floor" |
| `D_STOP_M` / `D_CLEAR_M` | 0.60 / 3.00 | §2.3 |
| `STEER_TTL_MS` | 400 | §4.4's most perishable row; a steer command ages like a car |
| `INTEGRATOR_K` | 0.35 per 100 ms tick | Shepherd stage 8, tuned in step 24 |
| `FAILSAFE_DECAY_MS` | 500 | Shepherd stage 9, verbatim — implemented as a fixed *rate* (full scale per 500 ms), not an exponential, because "the cue stopped" must be true at a known instant |
| `QUIET_PROXIMITY` / release | 0.35 / 0.30 (hysteresis) | step 24; on the §2.3 ramp that is onset at 2.16 m, release at 2.28 m — ~3 cane sweeps out. Below it **nothing** is emitted |
| `PULSE_RATE_MS` | 700 → 200 (far → near) | step 24; rate and stereo side carry the meaning, so no existing pattern can be confused with it |
| `NAV_BIAS_LIFETIME_MS` | 3000 | step 24; a navigation bias older than this stops contributing |
| `INTEGRATOR_K` behaviour | 50% at 161 ms, 90% at 534 ms | step 24, measured; `k_eff = 1 − (1 − k)^(elapsed/100)` so overshoot is structurally impossible and a jittering tick cannot retune the filter |

### 2.5 AMENDMENT (step 22, measured) — the one-parameter ground fit does not work

§2.3 above is **wrong on real frames** and is superseded by this section. Step 22 fitted it against a
real X4 equirect (`docs/steps/assets/step-06-live-equirect.jpg`, dewarped by `cane.bridge.dewarp`) and
**all three forward crops were rejected**: residuals 0.686 / 0.922 / 0.892 against the 0.25 threshold.

The reason is a property of the model, not of the fit: **Depth Anything V2 is affine-invariant in
disparity.** Its output is `a·(1/d) + b` for unknown `a` and `b`, and the offset dominates — measured
`b` = 2.5 / 3.5 / 3.8 against `a` = 3.7 / 0.9 / 1.4. A one-parameter fit that assumes `b = 0` cannot
recover scale: on the same data it scored rms 0.269 / 0.365 / 0.396 where a **two-parameter affine fit
scored 0.068 / 0.005 / 0.013** — between 4× and 70× better. Shipping the one-parameter scale anyway
would have placed a row whose true distance is 3.49 m at 2.92 m, so rejecting it was correct.

Three consequences, all live today:

1. **`anchored = false` is the primary path, not the fallback.** Anything built assuming metres will
   not run at the demo. `proximityFactor` is currently a named constant, not a measurement.
2. **`GapProfile` has no field that can carry a scale**, so even a perfect cloud-side fit cannot reach
   the bridge over the wire. Closing this is a contract amendment, not new maths.
3. **Gap *direction* is unaffected** and still fully trustworthy — ranking relative depth needs no
   scale, which is exactly the distinction §1.1 draws. The half of the steering law that says *which
   way* works; the half that says *how hard* is the one waiting on the scale.

The fix is **Step 27**. Note what it is not: fitting two parameters against ground-plane geometry is
still geometric recovery from a known mount height and a known focal length. It is not the §1.1 trap,
which is per-frame min-max normalisation of the depth map itself — that remains forbidden.

### 3.1 Rulings that came out of step 21 (these bind steps 22–24)

1. **`gap_bearing_deg` is contract-wide (−180, 180] but step 23 may only ever emit ±90.** The ring is
   forward-only, so the contract cannot catch a step-23 bug that emits +140. **Step 23 asserts its own
   ±90 invariant** in code and in a test; the contract stays wide so a future rear ring needs no
   amendment.
2. **When `blocked` is true, `gap_bearing_deg` points at the least-bad column** — the one with the
   most free distance even though it failed the `BODY_WIDTH_M` test. Rationale: a blocked wearer still
   benefits from a nudge toward the best available side, and a hardcoded 0.0 would steer them into
   whatever is dead ahead. `steer` itself is what says *how hard*; `blocked` is what says *do not
   trust this as a path*.
3. **At most one `GapProfile` per `crop_index`, and consumers index by `crop_index`, never by list
   order.** Step 22 enforces the uniqueness with a validator on `InferenceResponse`; step 23 must not
   assume the list is sorted or complete (a crop whose depth failed is simply absent).
4. **The envelope has eight frame shapes, not seven.** Step 21 found that the schema prose already
   said "six" while its `oneOf` listed seven (`diag` was added in step 20 without updating the text),
   and pinned the count with a test that derives it from `len(oneOf)`.
5. **A JSON integer where a float is expected must be accepted on the wire.** Swift's
   `JSONSerialization` writes `0.0` as `0`, and pydantic `strict=True` rejects that for a `float`
   field. Step 21 added a `_wire_number` BeforeValidator; steps 22–23 must use the same convention
   (`LenientFloat` / `_wire_number`) for every new numeric wire field rather than inventing a second
   one.

### 3.2 Rulings that came out of step 24 (controller decisions)

1. **A sixth `HapticSymbol` is approved**, notwithstanding step-10 Amendment 1's "exactly five".
   Amendment 1 bounds the symbols a wearer must *decode*; the steering tick is a continuous state
   signal — one 20 ms element (so it cannot be counted against left=2 / right=3), sharpness 0.20
   against the letters' 0.55, never selected for a hazard (pinned by a test), never spoken, and the
   weakest thing in the alphabet. It is inside the validated alphabet, so the existing
   no-two-scripts-alike rule *proves* the distinctness rather than a comment asserting it.
   **This overrides a teammate-owned spec, so it needs their nod before the freeze** — flagged, not
   assumed.
2. **Steering staleness must NOT feed `staleDrops`.** That counter is the one signal the §3.4 transport
   fallback reads, so folding steering drops into it would flip the phone to local inference because
   the *depth head* was slow. Separate counters, mirroring the existing `staleDrops` (arrived late) vs
   `queueExpired` (aged out while held) split. Step 24 got this right; it is now a rule.
3. **`steer` is not an angle.** It is dimensionless ±1 with proximity already folded in. The cue maps
   ±1 → ±90° (the ring edge) and goes through the existing `StereoPan` so cap, route gate and sign
   convention are the hazard ones. `gap_bearing_deg` is deliberately unrendered on the phone.
4. **`blocked` is not rendered by the cue and belongs to the voice layer (step 26).** §3.1 ruling 2
   makes it a *message* ("do not trust this as a path"), and a message is exactly what the haptic
   alphabet may not gain. Step 26's scope grows by this one item.
5. **The quiet gate deliberately bounds Shepherd's stage 6.** Because the cue is audible only at
   proximity ≥ 0.35, a navigation bias is never *rendered* above 0.65 weight — with nothing close it
   moves the integrator and is heard by nobody. That is intentional (navigation owns a speech channel;
   risk 3 forbids a chatty third), but it means **our merge is weaker than their always-on motor. Say
   that in the pitch rather than claiming a full merge.**
6. **`INTEGRATOR_K` stays 0.35, and the plan's "tuned in step 24" is downgraded to "measured".** No
   wearer was available. 534 ms to 90% is 0.75 m of walking against a 0.60 m stopping distance, so it
   is on the slow side; onset is what gets noticed and half the step lands in 161 ms. If step 25 shows
   lag, `k = 0.5` puts 90% at 332 ms and needs no other change.

### 3.3 Rulings that came out of step 23 (one of them is demo-critical)

1. **Ruling 3.1/1's ±90 was wrong, and as written it silently kills steering in the field.** The outer
   column centre is 88.125°, so **any mount offset above |`delta_deg`| = 1.875° rotates the ring past
   ±90 and every steer frame is dropped.** A shoulder-mounted X4 will have several degrees of offset,
   so this would have looked like "steering just never fires" at the venue. **Superseded by:**
   - the ±90 invariant applies to the ring in **camera-frame** bearings, *before* `delta_deg`;
   - the emitted `gap_bearing_deg` may legitimately span **±(90 + |delta_deg|)**;
   - `|delta_deg|` is bounded at **30°**, which keeps the widest possible bearing at 120° — so folding
     past ±180 (and `normalize_bearing_deg` silently flipping the gap's *side*) is structurally
     impossible rather than guarded against;
   - a `delta_deg` outside ±30 is a **mounting error and must fail loudly at startup**, never as a
     per-frame drop. No legitimate mount offset may ever cost a frame.
2. **`D_STOP_M` doing double duty is a real defect in the law, on the anchored path only.** As §2.3 and
   Step 23 are written, `D_STOP_M` is both the passability threshold and the ramp's saturation point,
   so the two halves of the steering law never co-vary: with a wall dead ahead at 1.2 m, nothing is
   within 0.6 m, every column is "passable", the widest run's centroid sits at 0° — and the frame says
   `blocked=false, steer=0.0` while `proximity` reads 0.75. **Step 27 must fix this when it turns
   metres on**: the gap direction has to be **depth-weighted** (lean toward the deepest part of the
   chosen run, and choose the run by free distance, not by column count) rather than the centroid of a
   binary passable run. Shepherd take direction from the column depths themselves, which is why they do
   not have this defect.
   The live unanchored path does **not** have this defect — its threshold self-scales — which is why
   the demo works today.
3. **Endorsed: the unanchored rule is a ratio of differences, and that is better than a metric
   threshold here.** `relative_freedom = (v_nearest − v)/(v_nearest − v_farthest)` cancels **both** `a`
   and `b` of the affine model exactly, so it is scale-*invariant* rather than scale-*ignorant*. Its
   0.5 cut equals the harmonic mean of the nearest and farthest distance in view. `proximity` stays the
   named constant 0.5 rather than a ramp over a placeholder scale — laundering a guessed metre through
   a band would have been precisely the silent fake §4.2 forbids.
   Two consequences, both pinned by tests and both to be said out loud: **a closing wall is invisible
   without an anchor** (a scale-free rule cannot tell 0.30 m in a 3 m view from 0.60 m in a 6 m view),
   and **a structureless ring reports `blocked`** — over-blocking is the safe way to be wrong.
4. **The `range_band` cross-check in §2.3 is nearly vacuous** and stays in only as a cheap guard: `near`
   is ≤ 5 m against a 0.6–3 m steering range, so only a `far` box over near depth can ever trip it, and
   it can false-positive on a far building above a near hedge. Do not count it as one of the two guards;
   the residual test is the one doing the work.
5. **Citation fix:** §4.2's "step 8, S1" does not exist in `docs/steps/step-08-brief.md`. The rule —
   never emit a fabricated detection when the real path is unavailable — is real and honoured; the
   label was mine. Cite step 8's brief generally, or the silent-failure review that produced it.

### 3.4 Rulings that came out of step 26

1. **Endorsed, and better than the plan asked for: the voice layer taps the `steer` frame, not the
   48-column ring.** `steering.py` had already reduced the ring to `gap_bearing_deg` + `blocked`, and
   those two *are* the summary the phone's cue is rendered from. Feeding the model anything else — a
   per-sector average, say — would be a second judgement over the same 48 numbers that could disagree
   with the buzz. This makes "the left is clear while the cue steers right" **structurally impossible
   rather than tested against**, and it inherits `steer_frame.py`'s refusals for free: a local-fallback
   bridge simply stops publishing, so the voice goes quiet about the ring instead of quoting a stale
   one (expiry `5 × STEER_TTL_MS` = 2000 ms).
2. **The wearer's vocabulary is comparative, never metric, and that is now enforced.** "Most open ahead
   to the right", "No clear path ahead, most open to your right", and — for a structureless ring —
   "No clear path ahead." with **no side claimed**, because "no clear path ahead, most open straight
   ahead" is a contradiction spoken aloud. A test sweeps every bearing × `blocked` × `anchored` and
   asserts no digit and no unit is ever produced, plus a guard asserting the anchored wording *equals*
   the unanchored one — so **step 27 has to break that test on purpose** to start speaking metres.
3. **The boundary on criterion 5, on the record:** we never *generate* a distance, and the model is
   instructed not to, but its prose is **not filtered**. Scrubbing metre-like substrings would mangle
   legitimate text, and a VLM's "a bench a few metres away" is its visual estimate, not our ring
   claiming a scale. The line: we never generate a metre, and the model's guess about which way is
   clear never overrides the ring.
4. **The frozen `answer` contract forces the clear side into words.** Only a `found` answer may carry
   `bearing_deg`, so a `described` answer has no numeric channel. "Direction stays geometric" is
   therefore honoured in *origin* — geometry picks the side, `service.py` computes it, no direction word
   crosses the model boundary in either direction — but not in *representation*. Inside the frozen
   shape that is the only option; accepted.
5. **`.navigate` naming the passable side is phone-side and is NOT done.** `.navigate` is an iOS intent
   and no wire shape carries a turn cue to the laptop, so step 26 delivered the substance where it was
   reachable (a `blocked` ring names the passable side on the `found` answer). The phone already
   receives `blocked` in the `steer` frame from step 24, so the real cue is a small **step 28**, below.
6. **Fixed in passing (not a ruling, a bug):** `IntentParser.parse("which way is clear")` returned
   `nil`, so the one question this system is now best placed to answer got "Sorry, I didn't catch
   that". Six phrasings now route to `describeFront` (`ios/CaneKit/Sources/CaneVoice/IntentParser.swift`,
   RED confirmed first, `ios/CaneKit` 434 tests green).

### 3.5 Rulings that came out of step 27 (the metric path now works — and exposed one more bug)

1. **The unanchored ring compares incommensurable numbers across crops, and can therefore claim a
   clear side that is not there.** `relative_freedom` cancels `a` and `b` **only within one crop**;
   each crop is its own inference with its own affine pair. Measured with the real pairs, a **uniform
   6 m scene reads 3.12 / 3.65 / 4.04** on C5 / C0 / C1 — so the ranking can report "free left,
   blocked right" for an empty corridor. Step 27 left it deliberately (acceptance 5 froze that path)
   and reported it instead.
   **Ruling: when the anchor is rejected, the ring must be restricted to the single forward crop C0** —
   one inference, one unit space, therefore sound — which is exactly Shepherd's own 60° coverage and
   already the documented fallback in risk 2. That is **step 29**, and until it lands, an unanchored
   frame's *side* is not trustworthy. This is now the strongest argument for the metric path being
   primary at the venue.
2. **Endorsed, and it fixed the same class of bug on the anchored path:** `smooth_ring` was running the
   kernel across crop seams on *raw* depth, i.e. averaging two unit systems. Measured, a seam column at
   a uniform 3 m came back as **2.03 m (32% error)** and — because `d_min` spans the arc — set
   `proximity` for the whole frame. Fixed at the root: on an accepted anchor each crop is converted
   with its own pair *before* assembly, so the ring is plain inverse metres and uniform scenes are
   uniform to 1e-9.
3. **The scale is two numbers per crop, not per frame.** Step 23's report said per frame; that was
   wrong, and one pair per frame would convert two crops in a third crop's units.
4. **The residual guard is blind to the three errors most likely at a venue, and this is the biggest
   risk the port leaves.** Measured: a 20% `MOUNT_HEIGHT_M` error gives a 20% distance error at
   residual **0.000**; a 20% up-slope gives 17% scale error at residual **0.000**; and a floor whose
   near half is hidden by a crowd inflated `a` **4×** at residual 0.056, *inside* the ceiling — i.e. it
   reports everything four times farther than it is, confidently. The `range_band` cross-check cannot
   catch it (it would need ~12×). **Operationally: measure the mount height, keep the camera level, and
   treat a crowded floor as a step-25 failure mode.** A split-half gradient guard was tried and
   rejected on measurement, not taste — a real crop's own halves disagree by 120%, worse than the
   synthetic occluded floor it was meant to catch.
5. **Endorsed: `contracts/` was correctly left untouched.** `GapProfile` is the *cloud↔bridge*
   contract; no JSON schema covers it and there is no Swift parser for it, and the `steer` frame already
   carried `proximity` and `proximity_anchored`, so nothing the phone parses changed. Step 27's text
   asking for "schema + golden fixture + both parsers" was two-thirds inapplicable — my error in
   writing it, not a shortcut taken.
6. **`ground_fit_residual` changed meaning while keeping its name**: it is now the fractional distance
   error the misfit implies at `D_CLEAR_M`. Pre- and post-step-27 residuals are not comparable
   (`new = old × 0.109`), and the threshold moved 0.25 → **0.15**, derived from the plan's own
   16%-error bar with 2.7× margin on the worst real crop.
7. **A uniform 1.2 m ring emitting `steer = 0.0` is correct, not the ruling-2 defect.** 1.2 m in all 48
   directions is a cylinder with no deepest part. Step 23's defect-pinning test asserted the wrong
   scene; the asymmetric case is now the regression test.
8. **`D_STOP_M`'s double duty is mitigated, not removed.** The weighting fixes the observable defect; a
   wall at 1.2 m is still formally "passable". A separate passability distance, or time-to-contact,
   remains open.

## 4. Inherited constraints — these bind every step below

1. **`contracts/` is frozen.** A new frame type is still a contract change: step 21 re-runs step 2's
   discipline (schema + golden fixture + Python parser + Swift parser + both test suites). The
   envelope already declares unknown `type` values as "log and ignore", so an old phone against a new
   laptop degrades silently instead of crashing — that property must be preserved and tested.
2. **No silent fakes** (step 8, S1). In local-fallback mode there is no depth, so the bridge **stops
   sending `steer`** and the phone decays to neutral. It never emits a zero steer as if it had looked.
3. **Hazard speech still beats navigation speech.** The §6 blend changes the *steering* mix, not the
   speech arbitration; `OutputArbiter` keeps its current priority.
4. **The cloud stays timeless** — no clock reads in the depth path, proven the way step 7's AST test
   proves it for detections.
5. **Latency budget.** Shepherd claims <150 ms sensing-to-motor. Ours is the existing X4→L4 round
   trip (p50 72 / p95 85–124 ms at the venue) plus the depth head; the depth head must stay **under
   25 ms for all three crops** on the L4 or step 22 has failed, because the whole point is that
   steering rides the frame we already send.
   **Measured 2026-09-26, before step 22 was written** (`backend/deploy/depth_crop_timing.py`, the exact
   `benchmarks.depth_model` loader step 22 reuses, 640-px crop, `input_size=280`, Apple MPS):
   **31.8 ms p50 per crop, 27.6 ms of it pure inference → 95 ms for three crops sequentially.** So on
   the laptop the three-crop cost is ~95 ms, which fits a 3 fps tick (333 ms) alongside local YOLO
   (~170 ms) but leaves little room; the 25 ms L4 target needs roughly the 3–4× TensorRT speed-up that
   YOLO saw (178 ms MPS → 12 ms TensorRT was ~15×), so it is plausible but unproven. Raw output on
   that run spanned 1.935…3.011 in arbitrary inverse-depth units — confirming §1.1: the model does not
   normalise per frame, `normalize_depth` in the benchmark does, and that function must stay out of
   the proximity path.

---

## Step 21 — Contract amendment: `gap_profile` in the cloud API, `steer` on the wire

Extend `backend/src/cane/cloud/api_models.py` with a `GapProfile` model (crop index, 16 column
values, band-filter rows used, ground-fit residual, `anchored` flag) on `InferenceResponse`, and add
a seventh envelope shape `steer` to `contracts/transport_envelope.schema.json`, its golden fixture,
`cane.transport.messages` and `CaneContracts`. `steer` carries `t_capture_ms`, `ttl_ms`, `steer`
(−1…+1, + = right), `proximity` (0…1), `gap_bearing_deg` ((−180, 180]), `blocked` (bool),
`proximity_anchored` (bool), `source` (`x4_cloud`). Both parsers reject out-of-range values and both
receivers ignore an unknown `type` without dropping the connection.

**Acceptance:** the new golden fixture round-trips byte-identically through the Python and the Swift
parser; a `steer` frame with `steer = 1.5`, a missing `ttl_ms`, or `bearing = -180.0` is rejected in
both languages; an unknown frame type is logged and ignored by both receivers; `backend` and
`ios/CaneKit` suites stay green.
**Out of scope:** computing any of these values; touching `awareness_event.schema.json`.

## Step 22 — Depth head in the cloud inference service

Run Depth Anything V2 Small on crops C5/C0/C1 inside the existing `/infer` call, reusing
`cane.benchmarks.depth_model`'s official-revision loader. Per crop: predict, apply the
`OBSTACLE_BAND_M` vertical filter using `focal_px` and `MOUNT_HEIGHT_M`, reduce to 16 column means of
**raw** relative depth (never per-frame normalised), fit the ground rows for the scale anchor, and
return a `GapProfile`. TensorRT on the L4 with a torch fallback, warmed by `/warmup`, and included in
the static engine build. The path reads no clock.

**Acceptance:** all three crops' depth adds **< 25 ms** to `/infer` on the L4 (measured, written into
`docs/steps/step-22-brief.md`); a synthetic frame with a wall on the left yields monotonically nearer
columns on the left; an AST test proves no clock call in the depth path; a depth model that fails to
load returns detections **with no gap profile** rather than a zero profile.
**Out of scope:** steering (23), the phone (24), depth on the rear crops.

## Step 23 — Obstacle detection, gap profiling and the steering law in the bridge

Assemble the three 16-column blocks into one 48-column ring in torso-relative bearings (crop yaw +
column offset + `delta_deg`), smooth with `SMOOTH_KERNEL` across the seams, mark columns passable
against `D_STOP_M`, pick the **widest contiguous passable run at least `BODY_WIDTH_M` wide** at its
own distance, and set `gapDirection` to that run's centroid normalised by 90°. Compute
`proximityFactor` per §2.3 with the residual and `range_band` cross-checks, then
`steer = sign(gapDirection) * sqrt(abs(gapDirection)) * proximityFactor`, `blocked` when no run
qualifies. Send one `steer` frame per tick with `ttl_ms = STEER_TTL_MS`; send none in local-fallback
mode.

**Acceptance:** a scripted left-wall profile steers right and a right-wall profile steers left, with
`abs(steer)` rising as the wall closes; a corridor with two equal gaps picks the one nearer ahead, not
the wider one by a pixel; a seam-straddling gap is found as one run, not two; flipping to local stops
`steer` frames entirely (no zero-steer frame is ever emitted); the ring's bearings span −90…+90° on a
live X4 frame.
**Out of scope:** the phone side (24); rear-crop depth; changing the detection path.

## Step 24 — The phone acts on it: integrator, navigation blend, failsafe decay

Consume `steer` in `CaneFeedback`, gate it on `STEER_TTL_MS` through the existing staleness
machinery, and feed a leaky integrator (`INTEGRATOR_K`) that `CaneOutput` renders continuously:
stereo pan = steer angle, haptic pulse rate = proximity, distinct from every hazard pattern. Blend
navigation additively as Shepherd does — the turn cue's lateral bias scaled by
`(1 - proximityFactor)` — replacing the current drop-the-cue rule for *steering* only while
`OutputArbiter`'s speech priority stays exactly as it is. When `steer` goes stale or the link drops,
decay the integrator to neutral over `FAILSAFE_DECAY_MS` and stop the cue.

**Acceptance:** a step change in `steer` reaches 90% of its target in the tuned time and never
overshoots; a stale `steer` decays to silence within 500 ms and nothing is held; with
`proximityFactor = 1` the navigation bias contributes zero; hazard announcements still preempt
everything; `ios/CaneKit` suite green with the new tests.
**Out of scope:** new haptic hardware; the pattern-preference study (step 10's `HapticTrial`).

## Step 25 — Live validation on the X4 and the numbers for the pitch

Walk the real rig through three set pieces — a corridor with one blocked side, a doorway, and a
person standing in the path — and record, for each: the 48-column ring, the chosen gap, `steer`,
end-to-end latency from `t_capture` to cue onset, and whether the wearer took the right side.
Compare against Shepherd's published <150 ms and note honestly where we sit. Update
`docs/orchestration-runbook.md` (status table, upcoming challenges) and write
`docs/steps/step-2{1,2,3,4}-brief.md` follow-ups with what changed under contact.

**Acceptance:** all three set pieces pass with the wearer taking the passable side unaided; measured
p50/p95 latency and depth-head cost written into the docs; at least one failure mode found and
documented (glass, a downward step, or a low overhang are the likely ones).
**Out of scope:** new features found during the walk — they go on the runbook, not into this step.

## Step 26 — Let the voice layer see the gap profile

Pass the current ring into `cane.scene`'s prompt so a `describe_front` or a "which way is clear"
question is answered from the same evidence the steering used, and let `.navigate` cues mention the
passable side when `blocked` is true. One prompt change, one plumbed argument, no new model.
**Scope grew by §3.2 ruling 4:** `blocked` is rendered nowhere else in the system — the haptic cue
cannot carry a sixth *message* — so announcing it is this step's job, and it is the only place a
wearer learns the path ahead is not trustworthy.

**Acceptance:** with a left-blocked fixture ring, the answer names the right side as the clear one;
Gemini latency stays inside the step-20 budget; with no ring available the prompt is unchanged from
today's and the answer still returns.
**Out of scope:** any new voice intent; any change to `IntentParser`.

## Step 27 — Recover metric scale with a two-parameter affine fit (unblocks `proximityFactor`)

Replace §2.3's one-parameter fit with the affine fit §2.5 measured: solve `z = a·(1/d) + b` over the
ground rows for both parameters, keep the residual guard and the `range_band` cross-check, and add the
recovered scale to `GapProfile` so the bridge can reach metres — a contract amendment in step 21's
style (schema + golden fixture + both parsers), since no existing field can carry it. Then switch the
bridge's metric path on at the seam step 23 left for it, so `proximityFactor` becomes a measurement
instead of a constant.

It must also fix §3.3 ruling 2 in the same pass — a metric path whose gap direction is the centroid of
a binary passable run emits `steer = 0.0` at a 1.2 m wall, so turning metres on without making the
direction depth-weighted would be a regression, not an improvement.

**Acceptance:** the affine fit reproduces step 22's measured rms (0.068 / 0.005 / 0.013) on the same
real equirect; a wall dead ahead at 1.2 m produces a non-zero `steer` toward the open side on the
anchored path (the §3.3 ruling 2 regression test); `anchored = true` on at least two of the three forward crops of that frame; a synthetic
frame with a known planted scale recovers it within 10%; with the scale present `proximityFactor`
varies with distance and `abs(steer)` rises as a wall closes in metres, not in arbitrary units; the
unanchored path still behaves exactly as step 23 shipped it when the fit is rejected.
**Out of scope:** any change to the §1.1 rule — per-frame min-max normalisation of the depth map stays
forbidden; the phone side (24) needs no change, since `proximity` was always 0…1 on the wire.

## Step 28 — 🔶 The navigation cue names the passable side (phone-side, small)

The phone already has `blocked` from the `steer` frame (step 24 consumes it but deliberately renders it
nowhere, per §3.2 ruling 4), and step 26 could only reach the *answer* path from the backend. So the
turn-cue half of §3.1 ruling 2 is still open: when `blocked` is true and a navigation cue is due, name
the passable side — "left turn ahead, keep right" — instead of a bare turn cue into a path the ring
says is not trustworthy. Entirely inside `CaneFeedback`/`CaneOutput`/`CaneNavigation`; no contract
change, no backend change.

**Acceptance:** a `blocked` steer plus a due turn cue yields a cue naming the passable side; an
unblocked steer leaves every existing cue byte-identical; hazard speech priority is untouched; the
wording carries no distance.
**Out of scope:** any new voice intent; any contract change; re-deciding the speech arbitration.

## Step 29 — Restrict the unanchored ring to the front crop (correctness, small)

Per §3.5 ruling 1 the scale-free rule is only sound *within* one crop, so when the anchor is rejected
the ring must be built from **C0 alone** (60°, one inference, one unit space) instead of three crops
whose numbers cannot be compared. Direction and `blocked` then mean what they say; coverage narrows to
Shepherd's own, which risk 2 already names as the acceptable fallback.

**Acceptance:** an unanchored uniform scene produces a symmetric ring and no phantom clear side (the
measured 3.12 / 3.65 / 4.04 case); an unanchored one-side-blocked scene inside C0 still steers
correctly; the anchored path is untouched; `gap_bearing_deg` stays within C0's ±30 when unanchored.
**Out of scope:** the anchored path; re-deriving `relative_freedom`.

## Step 30 — Cross-check the X4's scale against the phone's TrueDepth (closes §3.5 ruling 4)

The merge brought in `CaneCamera.TrueDepthProbe`: the phone's front camera is a TrueDepth sensor, and
it reports **a measured distance in metres straight ahead** — the only real metre anywhere in this
project. That is exactly what §3.5 ruling 4 says the affine fit is missing. The fit's residual is
blind to the three errors most likely at a venue (a wrong `MOUNT_HEIGHT_M`, a tilted camera, a floor
hidden by a crowd) because all three fit the ground rows *perfectly* while every distance is wrong —
the crowd case reported everything **4× farther** at a residual inside the ceiling.

TrueDepth cannot be fooled that way, and "straight ahead" is the centre of crop C0. So: send the
phone's reading up with the `health` frame (or a new one), compare it to the anchored distance at C0's
centre column, and reject the frame's anchor when they disagree by more than a set factor. One real
metre is worth more than any amount of self-consistency checking.

**Acceptance:** a planted 20% mount-height error is *caught* (it currently scores residual 0.000); the
occluded-floor case that inflated `a` 4× is caught; agreement within the tolerance leaves the anchor
accepted and changes no behaviour; a phone with no TrueDepth (SE, pre-X) degrades to today's path
rather than refusing to anchor.
**Out of scope:** using TrueDepth for steering itself — its useful range is short and it sees only
ahead, which is the phone's sector already; this is a *guard*, not a second depth source.

---

## Risks, ordered by how much they threaten the demo

1. **The scale anchor was the plan's soft spot, and it broke exactly there — confirmed by
   measurement, not guesswork.** See §2.5: the one-parameter fit is rejected on every real crop, so
   `blocked` and gap *direction* are what ship today and `proximityFactor` is a constant. This risk
   has therefore already materialised; step 27 is the recovery, and the demo is safe without it
   because direction is the half that steers. The residual risk is now narrower: that the
   *two-parameter* fit also fails outdoors, where a 60° crop only sees the floor between 2.42 m and
   3.49 m and a crowd hides even that. If step 27's fit is rejected at the venue, the constant stands
   and we say so in the pitch rather than faking a number.
2. **25 ms for three depth passes on the L4 is still an assumption.** It is now bounded on one side:
   measured 31.8 ms per crop / 95 ms for three on Apple MPS (§4.5). The L4 needs a ~4× TensorRT win
   to reach 25 ms. If it misses, drop to C0 alone (16 columns, 60°) — which is exactly Shepherd's own
   coverage, so the demo still matches the reference design and the ring code is unchanged.
3. **A continuous cue competes with speech for the wearer's attention.** Ours is the third audio
   channel after hazards and navigation. Step 24 must keep it quiet when `proximityFactor` is low, or
   the demo becomes noise.
4. **Two new frame shapes in a frozen contract.** Step 21 is unglamorous and gates 22–24; do not let
   it be skipped "just for the demo", because a silently mismatched parser on the phone is the one
   failure nobody will debug on stage.
