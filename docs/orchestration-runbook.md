# Orchestration Runbook — 19 steps, ready to paste

**Plan:** `docs/build-plan.md` + `camera-sync-design.md` + `device-responsibilities.md` + the master PDF
**ECC mode:** plugin · **Lang:** polyglot (Swift ×2 lanes, Python ×1) — reviewer assigned per step
**Steps:** 19 · **Scope:** all
**Last updated:** 2026-09-26

> **One deviation, deliberate.** The only registered orchestrate command in this install is
> `/everything-claude-code:orchestrate` (its sibling `ecc:orchestrate` is a legacy shim that just
> redirects to skills). But the `everything-claude-code` namespace ships **13 agents** and has no
> Swift reviewer, while the `ecc` namespace ships **67** and does. So: the command comes from one
> namespace, the agents from the other. Both resolve — `ecc:*` names are fully qualified, so there
> is no fuzzy matching. Two of the three lanes write Swift; using `code-reviewer` for all of it
> would have been the worse trade.

## Build status — 2026-09-26 (re-verified end to end that afternoon)

**Lane assignment.** P1 (teammate): steps 4, 5 and scene segmentation — PR #1 landed the laptop *baselines* for both plus step 10's `CaneOutput` logic. Claude: 6 → 7 → 8, 9, 12 — **all five built, reviewed, fixed and on `main`**. **7 of 17 core steps are done** (2, 3, 6, 7, 8, 9, 12); 1 and 10 are partial; 4, 5, 11, 13, 14, 15, 16 and 19 are open. The whole laptop half is proven live; the phone half has no app to run in yet — see **Upcoming challenges**.

| Step | State | Where |
|---|---|---|
| 2 · Event contract + golden fixtures | **built, reviewed, on `main`** | `contracts/`, `backend/src/cane/contracts`, `ios/CaneKit/Sources/CaneContracts` |
| 3 · NTP handshake + WebSocket transport | **built, reviewed, on `main`** | `backend/src/cane/transport`, `ios/CaneKit/Sources/CaneTransport` |
| 6b · Six-crop preview | **built** — `cane.benchmarks.crops` shows the real dewarped crops (AVFoundation equirect → `cane.bridge.dewarp`) tiled Front-L/Front/Front-R over Rear-L/Rear/Rear-R, detections tagged by direction. Replaces looking at the X4's raw dual-fisheye, which OpenCV is limited to on macOS and which nothing in this system processes. ~500 ms/frame for six local YOLOE passes — a debug view, not the pipeline | `backend/src/cane/benchmarks/{crops,crop_layout}.py` |
| 6 · X4 bridge | **built, live-tested on the X4, reviewed (Python + performance), fixed, on `main`** — frame age at send p50 13 / p95 36–43 ms under an 800 ms uplink with never more than one request in flight; after review the age is sampled at the wire, a 100 ms frame deadline drops late frames, and the freshness retry is bounded. Re-measured with wire-side sampling: p50 12–13 / p95 48–51 ms at rung 1 on an idle uplink, p95 25 ms under the 800 ms uplink (the health monitor stepped to rung 3 by itself) | `backend/src/cane/bridge`, `docs/steps/assets/step-06-live-*.jpg`, `docs/steps/step-06-brief.md` |
| 7 · Cloud inference service | **built, reviewed (ML + Python), fixed, live-tested, on `main`** — 153 tests; fix round landed (tracks start at the emit threshold, bounded multipart read, closing-speed deadband and sign gate, weights baked outside the persistent mount); real YOLO11s on this Mac's GPU drove the X4 end to end (177 frames, one person track stable for 164 frames, 638 valid `x4_cloud` events; inference p50 178 / p95 369 ms locally, ~7× the L4 budget as expected). Run locally: `cd backend && UV_PROJECT_ENVIRONMENT=.venv-yolo CANE_DETECTOR=ultralytics CANE_DEVICE=mps PYTHONPATH=src uv run --extra yolo uvicorn cane.cloud.app:app --port 8000`, then `GET /warmup` (first call ≈ 11 s). **Cloud VERIFIED on the L4 (evening):** TensorRT model 12 ms at 640 / 20 ms at 512, service ≈ 30 ms incl. JPEG decode; from the venue the real X4 bridge measured round trip p50 72 ms / p95 85–93 ms at 3 fps with zero failures (PyTorch-on-CUDA mode: 91 ms). The dynamic engine build failed on TensorRT 10.13, so the export self-heals to a static batch-6 engine; `numpy<2` + `opencv<5` were required in the image. VM `l4-infer` (`us-east1-d`), disk `cane-models`, firewall to the laptop's /32 only — `backend/deploy/README.md` has the exact commands and the start/stop routine | `backend/src/cane/cloud`, `docs/steps/step-07-design.md`, `docs/steps/assets/step-07-live-detections.jpg` |
| 8 · Health monitor + local fallback | **built, reviewed (Python + silent-failure hunt), fixed, on `main`** — the three-strike flip to local was verified live against the real service and recovery lands at the pre-flip rung + 1, then climbs one rung per 30 s healthy; a broken local detector fails fast (never a silent fake); local inference is warmed at start, bounded at 2 s, and guarded against concurrent entry. **Proven live on the X4 with real YOLO11s on both sides (10:41–10:44):** cloud mode at 2.2–2.7 fps with inference p50 158 / p95 200–400 ms; service killed → `cloud(1) -> local` 1.9 s later after exactly three strikes, send rate unbroken, local inference p50 ≈ 170 / p95 ≈ 250 ms; service restarted and warmed → `local -> cloud(2)` 30 s after the first healthy probe. Local fallback = YOLO11s `.pt` on Apple MPS; the CoreML export is blocked by a coremltools/torch version mismatch. Run in `.venv-yolo`: `python -m cane.bridge --source x4 --rung 1 --inference-url http://<cloud>:8000/infer --local-detector ultralytics --local-weights yolo11s.pt` | `docs/steps/step-08-brief.md`, `backend/src/cane/bridge/{backends,health,probe,uplink}.py` |
| 9 · Feedback manager gauntlet | **built, reviewed, fixed, on `main`** — 126 tests, actor + gauntlet per the design; step 10 must `tick` the manager and give hazard speech priority over navigation | `ios/CaneKit/Sources/CaneFeedback`, `docs/steps/step-09-*.md` |
| 12 · Navigation (Routes + turn cues) | **built, reviewed, fixed, live-checked, on `main`** — 115 tests; `nav-cli` resolved FIU Graham Center → Green Library through Places and Routes (261 m, 4 steps) and, after a heading-aware corner fix found on that run, the simulated walk plays exactly the seven expected cues. Re-run any time with `GOOGLE_MAPS_API_KEY=… swift run nav-cli "<origin>" "<destination>"` (key stays in the env, never in the repo) | `ios/CaneKit/Sources/CaneNavigation`, `docs/steps/step-12-brief.md` |
| 10 · Haptics + TTS | **built** — the pure logic (`BearingSector`, `HapticScript`, `SpeechPhrase`, `SpeechSettings`, `StereoPan`, 35 tests) came from the teammate's PR #1; the playback half (CoreHaptics renderer, `AVSpeechSynthesizer` renderer with watchdog, audio session with `.playback` fallback, `OutputArbiter`, `OutputSink`, the tick loop driving `CaneFeedback`) came from `feat/phone-app` and is built on that logic. Still open: the Amendment 7 `HapticTrial` harness | `ios/CaneKit/Sources/CaneOutput`, `docs/steps/step-10-*.md` |
| 20 · Phone app | **built and running on a phone** (iPhone 17 Pro; iPhone 13): XcodeGen app, outward "reactor" chest display, open mic with "Hey Cane", shared front camera, query/answer frames, laptop `cane.scene` answering with Gemini over the six crops. Hardware acceptance in the brief §F | `ios/CaneApp`, `ios/CaneKit/Sources/Cane{Voice,Camera,Assist}`, `backend/src/cane/scene`, `docs/steps/step-20-brief.md` |
| 21 · Steering contract (`gap_profile` + `steer`) | **built, TDD, in the working tree** — `GapProfile` (16 raw relative-depth column means, `band_rows`, ground-fit residual, `anchored`) on `InferenceResponse`, and an eighth envelope frame `steer` (`t_capture_ms`, `ttl_ms`=400, `steer` −1..1, `proximity` 0..1, `gap_bearing_deg`, `blocked`, `proximity_anchored`, `source`) in both languages with a 20-case golden fixture. Found and fixed a pre-existing drift: the schema prose said "six" shapes while its `oneOf` already listed seven (`diag` landed in step 20 without updating the text) — the count is now derived from `len(oneOf)` by a test, so the prose cannot drift again | `contracts/`, `backend/src/cane/{cloud/api_models,transport/messages}.py`, `ios/CaneKit/Sources/CaneTransport/SteerCommand.swift`, `docs/shepherd-steering-plan.md` |
| 22 · Cloud depth head | **built, TDD, in the working tree** — Depth Anything V2 Small on the three forward crops inside `/infer`, reusing `benchmarks.depth_model` verbatim; obstacle-band filter on `dewarp.focal_px`, 16 **raw** column means (a test pins that a constant depth offset changes the columns, so the §1.1 normalisation trap cannot creep back), ground fit, `/warmup` warms depth, TensorRT path carries an UNVERIFIED header. 399 tests in `tests/cloud`, torch-free. Measured on a real X4 equirect on MPS: **three crops p50 92.2 ms**, of which the head's own arithmetic is **1.69 ms** — so essentially the whole budget is inference, and the 25 ms L4 target needs a ~3.6× TensorRT win (**unknown until the L4 runs it**). Caught that `inference_ms` now includes depth, so pre- and post-step-22 latency numbers are not comparable | `backend/src/cane/cloud/depth/`, `docs/steps/step-22-brief.md` |
| 27 · Affine scale fit | **OPEN, and it is the one that matters** — step 22 measured the plan's one-parameter ground fit against a real frame and **all three crops were rejected** (residuals 0.686/0.922/0.892 vs 0.25). Depth Anything V2 is affine-invariant in disparity, so a two-parameter fit is required: on the same data it scored rms 0.068/0.005/0.013. Until this lands, `anchored` is false in the field and `proximityFactor` is a named constant — gap *direction* still works, so the demo stands, but the steering magnitude is not yet a measurement | `docs/shepherd-steering-plan.md` §2.5 + step 27 |
| 24 · Phone steer consumer | **built, TDD, in the working tree** — `ios/CaneKit` now **433 tests, 0 failures** (385 before). Shepherd's stages 6/8/9: a pure `LeakyIntegrator` (`k_eff = 1 − (1 − k)^(elapsed/100)`, so overshoot is structurally impossible and a jittering tick cannot retune it — 50% at 161 ms, 90% at 534 ms), the `(1 − proximityFactor)` navigation blend, a steering cue whose meaning is carried by **repetition rate (700→200 ms) and stereo side** rather than burst shape, and a failsafe that decays at a fixed rate so "the cue stopped" is true at a known instant (silence ~350 ms, neutral at exactly 500 ms). Quiet below proximity 0.35 (= 2.16 m, ~3 cane sweeps), release 0.30. Speech arbitration untouched, with three tests pinning that hazard preempts steering and the 1800 ms post-hazard gap silences it | `ios/CaneKit/Sources/Cane{Feedback,Output}`, `docs/steps/step-24-brief.md` |
| 23 · Bridge gap ring + steering law | **built, TDD, in the working tree** — 48-column ring in torso bearings with `SMOOTH_KERNEL` across the seams, two passability rules, run finding, Shepherd's `sign·sqrt(|gapDirection|)·proximityFactor`, and one `steer` frame per tick (none in local fallback, asserted as a count). 103 new tests; `tests/bridge tests/transport` **533 passed, 1 skipped**. The live path is the **unanchored** one: `relative_freedom = (v_nearest − v)/(v_nearest − v_farthest)` cancels both `a` and `b` of the affine model exactly, so it is scale-*invariant* rather than scale-ignorant, and `proximity` stays a named 0.5 rather than a ramp over a guessed metre. Local backend now builds with `depth=None` (a `GapProfile` nobody sends is not worth 92 ms/tick). Found the demo-critical `delta_deg` > 1.875° frame-drop bug — see §3.3 ruling 1 | `backend/src/cane/bridge/{gap_ring,gap_anchor,steering,steer_frame}.py`, `docs/steps/step-23-brief.md` |
| 26 · Voice layer reads the gap ring | **built, TDD, in the working tree** — `tests/scene` **90 passed, 1 skipped**; `tests` minus `tests/bridge` **802 passed, 1 skipped**. Rather than plumb 48 columns, it taps the **`steer` frame the phone was already sent**, so the words and the buzz come from one number and "the left is clear while the cue steers right" is structurally impossible. Prompt grows by a bounded **+246 chars ≈ 62 tokens** (asserted, with a max-4-numbers guard so it can never become a float dump) against six JPEGs that dominate by an order of magnitude. Wearer hears comparative language only — "Most open ahead to the right", "No clear path ahead, most open to your right", and no side at all for a structureless ring — with a sweep asserting no digit or unit is ever generated. Also fixed: `"which way is clear"` parsed to `nil` before this, so the one question the system is now best placed to answer got "Sorry, I didn't catch that" | `backend/src/cane/scene/{gap,gap_speech,prompt,service}.py`, `ios/CaneKit/Sources/CaneVoice/IntentParser.swift`, `docs/steps/step-26-brief.md` |
| 27 · Affine scale fit | **built, TDD, in the working tree — `proximityFactor` is now a measurement.** Least-squares affine fit `z = a·(1/d) + b`; on the real X4 frame **3 of 3 forward crops anchor**, rms **0.0679 / 0.0049 / 0.0133** reproducing plan §2.5 exactly (independently re-verified: `deploy/affine_ground_fit_check.py` → ACCEPTANCE 1 PASS). Uniform scenes at 3.0/2.4/1.8/1.2/0.6 m now give `proximity` 0.00/0.25/0.50/0.75/1.00 where step 23 gave the constant 0.5. Ruling-2 defect fixed by depth-weighting: a 1.2 m wall with an open right went `steer` **0.0000 → +0.5222** with the *same* chosen run, proving it was the weighting. Also found and fixed a real bug: `smooth_ring` was averaging across crop seams in **two different unit systems** (a seam column at a uniform 3 m read 2.03 m, 32% off, and set `proximity` for the whole frame) | `backend/src/cane/cloud/depth/profile.py`, `backend/src/cane/bridge/{gap_anchor,steering,steer_frame}.py`, `docs/steps/step-27-brief.md` |
| 29 · Unanchored ring → front crop only | **OPEN, correctness** — `relative_freedom` cancels the affine pair only *within* a crop, so across three it compares incommensurable numbers: under the measured pairs a **uniform 6 m scene reads 3.12 / 3.65 / 4.04**, i.e. the unanchored path can claim "free left, blocked right" in an empty corridor. Fix is to use C0 alone when unanchored (Shepherd's own coverage). Until then, **an unanchored frame's side is not trustworthy** | `docs/shepherd-steering-plan.md` §3.5 ruling 1 + step 29 |
| 1 · Spikes | **spike 1 CLOSED** (X4 is UVC; 2880×1440 streams at ~29 fps through the AVFoundation source; OpenCV cannot select that mode). Spikes 4, 5, 7, 8, 9 still team-run | §6 of `camera-sync-design.md` |
| 4, 5 · Phone YOLO + depth | **not built on the phone.** Groundwork landed instead (teammate, PR #1): `cane.benchmarks` gives laptop baselines for YOLOE and Depth Anything V2 Small plus a fused objects+depth preview, measured 36 / 53 / 127 ms p50 on MPS. These are reference numbers and a tuning surface, not the iOS implementation | `backend/src/cane/benchmarks`, `docs/{yolo,depth}-benchmark.md`, `docs/fused-preview.md` |
| 11, 13–19 | runbook entry only | below |

Rules of the road: `contracts/` is frozen (a change = re-running step 2 in both languages); every step's binding spec is its `docs/steps/step-NN-brief.md` plus `-design.md` where present, and the design's amendments override the brief. Test counts today: `backend` 589 passed (+1 live-X4 test gated on `CANE_LIVE_X4=1`, +1 latency test that self-skips under machine saturation), `ios/CaneKit` 312. **After steps 21–24 (2026-09-26, full suites re-run from the integrated tree): `backend` 1096 passed / 2 skipped, `ios/CaneKit` 433 passed / 0 failures.**

### Full re-verification — 2026-09-26 afternoon

Everything below was re-run from a clean tree after the teammate's PR #1 merged, so these are
today's numbers, not carried forward.

| Check | Result |
|---|---|
| `backend` suite | **589 passed**, 2 skipped (live-X4 gate; latency test self-skips under load) |
| `ios/CaneKit` suite | **312 passed**, 0 failures |
| Live X4 capture (`CANE_LIVE_X4=1`) | 5/5, AVFoundation 2880x1440 |
| Cloud L4 warm model | 11.9 ms @640 / 16.7 ms @512, TensorRT, engine cache hit |
| X4 → L4 → stand-in phone | **404 events, 0 rejected, 0 contract violations**, 19 tracks, bearings −180…+180, rtt **p50 77 / p95 124 ms**, 2.9 fps, 0 failed, in-flight never > 1 |
| Step-8 fallback drill (localhost) | flip in **1.1 s** after exactly 3 strikes; recovery to pre-flip rung + 1 |
| Navigation live (Routes + Places) | 261 m / 4 steps, exactly the seven expected FIU cues |
| YOLOE / depth / fused benchmarks | 36 / 53 / 127 ms p50 frame on MPS; real detections on a real frame |

The last hop had never been driven live before: a stand-in WebSocket client answered the NTP pings
and validated every event with the repo's own `parse_awareness_event`. Zero violations, and the
bearing spread proves all six crops contribute.

### Upcoming challenges

Ordered by how much they threaten the demo.

1. **Resolved by `feat/phone-app` (step 20): the iPhone app now exists** — `ios/CaneApp` wires `CaneTransport → CaneFeedback → CaneOutput → CoreHaptics/AVSpeech`, runs on a phone, and adds the open mic and the Gemini questions (`docs/steps/step-20-brief.md`). Phone YOLO + depth (steps 4, 5) plug into `CaneCamera.FrontCamera` and are still to build. *Original note:* **There is no iPhone app — this is the critical path.** No `.xcodeproj`, no app target, no
   `@main`. `CaneKit` is a library, and `CaneOutput` describes haptic patterns and speech phrases
   without importing CoreHaptics or AVSpeechSynthesizer, so nothing plays. The pipeline currently
   produces contract-valid events that **have no consumer on any real device** — a stand-in script
   was the only way to prove delivery. Steps 4, 5, 10, 11 and 13 all sit behind this one missing
   piece. A thin app shell wiring `CaneTransport → CaneFeedback → CaneOutput → CoreHaptics/AVSpeech`
   converts all the proven backend work into something demoable and unblocks four steps.
2. **The benchmark model paths are untested.** The 50 benchmark tests use a fake `cv2` and never
   import torch, ultralytics or CLIP. Fusion maths, colour scale and toolbar state are covered;
   model loading and inference are not. "587 green" must not be read as "the depth benchmark runs".
   *Partly closed 2026-09-26:* the **depth** loader and a real forward pass were executed directly
   (`backend/deploy/depth_crop_timing.py`) — Depth Anything V2 Small loads from
   `third_party/Depth-Anything-V2` + `checkpoints/depth_anything_v2_vits.pth` and runs at **31.8 ms p50
   per 640-px crop at `input_size=280` on Apple MPS** (27.6 ms pure inference), raw output in arbitrary
   inverse-depth units. The YOLO and CLIP paths remain unexecuted by tests.
3. **Prompt-list leftovers.** `DEFAULT_CLASSES` still contains `waymo` and `mouse`; `mouse` fired as
   a false positive on a bowl and again on the live X4 feed. On a cane a phantom obstacle is worse
   than a missed one.
4. **Fallback can oscillate when the cloud is marginal.** Recovery to cloud immediately re-flipped
   during the localhost drill. That specific case is a harness artefact — "cloud" and the local
   detector shared one Apple GPU — and cannot happen against the L4 (separate GPU, 69–124 ms rtt).
   There is no flap damping beyond the 30 s healthy-probe rule, which bounds real oscillation to one
   cloud attempt per 30 s. Acceptable, but know it is the behaviour.
5. **Operational fragility.** The X4 drops off USB when its screen sleeps (set its timers to never);
   the L4's external IP is ephemeral and the firewall pins one /32, so both need re-checking if the
   venue network or a stop/start changes them. CoreML export stays blocked on the coremltools/torch
   mismatch, so the local fallback is `.pt` on MPS at ~170 ms.
6. **Two tests are load-sensitive by design** and self-skip or wobble under a saturated machine: `tests/cloud/test_app.py`'s non-inference latency budget (skips above one job per core, printing the number) and the 2 s real-time fallback test. A third, the synthetic burned-in counter, was genuinely flaky at ~37% and is now fixed. If a suite goes red during a parallel build, check the load average before chasing the code.
7. **Steps 15 (Firestore) and 16 (latency benchmark) are unstarted** but cheap and do not block the
   demo. 11, 13, 14 and the 🔶 stretch items 17/18 are untouched.

### Demo decisions (controller, 2026-09-26 — the user delegated these)

1. **Cloud or local for the walk.** Measured at the venue on 2026-09-26: cloud round trip p95 85–165 ms, so **cloud is the primary**. At rehearsal, run the bridge for 60 s against the L4 and read `rtt p95` from its stats line. If p95 ≤ 300 ms, start in cloud at rung 1 and let the health monitor handle saturation. If p95 > 300 ms, start with `--start-mode local --local-detector ultralytics`: local end-to-end measured p50 ≈ 170 / p95 ≈ 250 ms today, which keeps the 500 ms cyclist TTL with margin, whereas cloud p95 of 400–600 ms would drop cyclists as stale. The TTL table stays as written (§4.4). Run `backend/deploy/warmup_loop.sh` from the moment you walk toward the stage, and keep it running.
2. **Hazard speech beats navigation speech.** Step 10's sink presents hazard announcements first; navigation turn cues (`prepare`, `now`, `correctHeading`, `continueStraight`) that arrive while a hazard announcement is presenting or inside its 1800 ms speech gap are **dropped**, not queued — a late turn cue is a wrong cue. `arrived`, `offRoute`, `gpsLost`, `gpsRestored` are re-issued on the next tick after the gap. Hazard haptics preempt navigation haptics; navigation uses the distinct accent H-12a requires.
3. **Recovery rung.** After a fallback the bridge returns to the pre-flip rung plus one (never below rung 4) and climbs one rung per 30 s healthy — implemented in step 8.
4. **Local detector.** YOLO11s `.pt` on Apple MPS is the fallback; the CoreML export stays deferred (coremltools 9 does not yet support this torch).

### Refreshed invocations for the steps that now have briefs

These supersede the older commands below for steps 6, 7, 8, 9 and 12: the plan path points at the brief (or design), which is the binding spec.

```bash
/everything-claude-code:orchestrate custom "ecc:tdd-guide,ecc:performance-optimizer,ecc:python-reviewer" "[Plan: docs/steps/step-06-brief.md#step-6] Build the X4 bridge per rulings F1–F6: AVFoundation 2880×1440 capture into a single-slot latest-frame store on a dedicated thread, six 60° remap-LUT crops, JPEG per rung, a one-in-flight sender, bearing = yaw + (cx−0.5)·60 + delta_deg normalised into (−180, 180], t_capture from the phone clock, events to EventLinkServer; api_models per step-07-design A2. Acceptance: frame age at send p95 < 50 ms with an 800 ms uplink and in-flight never > 1, measured live on the X4; stripe tests pin the LUT geometry; the e2e test delivers a valid x4_cloud event to a fake phone. Out of scope: the inference server (7), rung stepping and local fallback (8), Firestore (15)"
/everything-claude-code:orchestrate custom "ecc:tdd-guide,ecc:mle-reviewer,ecc:python-reviewer" "[Plan: docs/steps/step-07-design.md#step-7] Implement the inference service per amendments A1–A15 (they win over the brief): FastAPI /infer /healthz /warmup over a FastAPI-free InferenceService; per-(session, crop) supervision ByteTrack with rung-fps eviction and generation-stamped track ids; pinhole range → band and glitch-rejecting closing speed; a timeless request path proven by an AST test; CANE_DETECTOR=ultralytics on MPS for the laptop; tier-2 Docker/L4 files carry the UNVERIFIED header. Acceptance: tests green under -W error with no torch in .venv; t_capture_ms echoed byte-identical and no range_m in the raw body; track_id stable across 30 frames; a live X4 → bridge → service run emits x4_cloud events. Out of scope: urgency, OCR, faces, the bridge (6), fallback (8)"
/everything-claude-code:orchestrate custom "ecc:tdd-guide,ecc:silent-failure-hunter,ecc:python-reviewer" "[Plan: docs/steps/step-08-brief.md#step-8] Implement the health monitor per rulings E1–E5: one InferenceBackend interface with a CloudBackend and an in-process LocalBackend that reuses step 7's InferenceService; a pure HealthMonitor (step down at p95 RTT > 500 ms over 10 frames; flip to local on 3 consecutive timeouts, rung 4 still failing, or phone stale-drop rate ≥ 0.2; recover one rung per 30 s healthy via /healthz probes); dynamic rungs in the pipeline; the health message from the phone. Acceptance: induced timeouts flip to local within 3 frames; upward steps at most once per 30 s; identical scripted detections yield identical events through cloud and local except source and track_id. Out of scope: the service (7), Firestore (15), the Swift sender of the health message"
/everything-claude-code:orchestrate custom "ecc:architect,ecc:tdd-guide,ecc:swift-reviewer" "[Plan: docs/steps/step-09-brief.md#step-9] Build CaneFeedback per the brief and design amendments A1–A17: public validating AwarenessEvent init first, then validate → staleness → exact-duplicate → dedup → urgency (total function, confidence-gated, announced-anchored hysteresis) → refractory → keyed bounded queue → pump with dequeue re-gate, all inside one actor with bounded state. Acceptance: the golden fixture incl. malformed events passes; a flood of one track_id yields one announcement; urgency 1 never speaks; a stale event is never announced late"
/everything-claude-code:orchestrate custom "ecc:docs-lookup,ecc:tdd-guide,ecc:swift-reviewer" "[Plan: docs/steps/step-12-brief.md#step-12] Build CaneNavigation per rulings F1–F7 and the section-H API reference: Routes API v2 WALK with the mandatory field mask, a Places Text Search resolver with Geocoding fallback, the 1e5 polyline decoder, a route follower, the turn-cue policy with the F3 constants, a NavigationEngine actor, an iOS-only CoreLocation adapter and nav-cli. Acceptance: the two-turn fixture walk yields exactly prepare-left, now-left, prepare-right, now-right, arrived; GPS loss emits one cue then silence while the actor stays alive; the target has no awareness-path dependency. Out of scope: haptic pattern design (10), voice (11), the map view, last-50-feet (17)"
```

---

## Steps overview

| # | Title | Lane | Tags | Chain |
|---|---|---|---|---|
| 1 | Pre-flight spikes, keys, SDK application | all | plan, lookup | `ecc:planner,ecc:docs-lookup` |
| 2 | Event contract + golden fixtures | all | design, impl | `ecc:architect,ecc:tdd-guide,ecc:python-reviewer,ecc:swift-reviewer` |
| 3 | NTP clock handshake + WebSocket transport | P1+P2 | impl | `ecc:tdd-guide,ecc:python-reviewer,ecc:swift-reviewer` |
| 4 | Phone capture + intrinsics FOV + YOLO + bearing | P1 | impl | `ecc:tdd-guide,ecc:performance-optimizer,ecc:swift-reviewer` |
| 5 | Depth Anything V2 + ground-plane scale anchoring | P1 | impl | `ecc:tdd-guide,ecc:mle-reviewer,ecc:swift-reviewer` |
| 6 | X4 latest-frame grab + 6-crop dewarp + sender | P2 | impl | `ecc:tdd-guide,ecc:performance-optimizer,ecc:python-reviewer` |
| 7 | Cloud L4 TensorRT + ByteTrack + closing speed | P2 | impl | `ecc:architect,ecc:tdd-guide,ecc:mle-reviewer,ecc:python-reviewer` |
| 8 | Health monitor, rung stepping, CoreML fallback | P2 | impl | `ecc:tdd-guide,ecc:silent-failure-hunter,ecc:python-reviewer` |
| 9 | Feedback manager gauntlet | P3 | impl, test | `ecc:architect,ecc:tdd-guide,ecc:swift-reviewer` |
| 10 | Haptics + TTS + stereo panning output | P3 | impl | `ecc:a11y-architect,ecc:tdd-guide,ecc:swift-reviewer` |
| 11 | Voice intent recognition | P3 | impl | `ecc:tdd-guide,ecc:swift-reviewer` |
| 12 | Navigation: Routes API + turn cues | P3 | impl, lookup | `ecc:docs-lookup,ecc:tdd-guide,ecc:swift-reviewer` |
| 13 | Sign and text reading | P3 | impl | `ecc:tdd-guide,ecc:swift-reviewer` |
| 14 | VLM scene description, front + around | P2+P3 | impl, lookup, security | `ecc:docs-lookup,ecc:tdd-guide,ecc:swift-reviewer,ecc:security-reviewer` |
| 15 | Hazard map Firestore writes | P2 | impl, db, security | `ecc:tdd-guide,ecc:python-reviewer,ecc:security-reviewer` |
| 16 | Latency instrumentation + benchmark slide | P2 | test, impl | `ecc:tdd-guide,ecc:performance-optimizer,ecc:python-reviewer` |
| 17 | 🔶 Last-50-feet navigation | P3 | impl, lookup | `ecc:docs-lookup,ecc:tdd-guide,ecc:swift-reviewer` |
| 18 | 🔶 ADK Workflow orchestration + A2A agent | P2 | design, impl, lookup | `ecc:docs-lookup,ecc:architect,ecc:tdd-guide,ecc:python-reviewer` |
| 19 | Integration, demo route, freeze, pitch | all | plan, docs | `ecc:planner,ecc:doc-updater` |
| 20 | Phone app shell, chest display, open mic | P3 | impl | `ecc:tdd-guide,ecc:swift-reviewer` |
| 21 | Steering contract: `gap_profile` + `steer` frame | P2+P3 | impl | `ecc:tdd-guide,ecc:python-reviewer,ecc:swift-reviewer` |
| 22 | Depth head in the cloud inference service | P2 | impl, perf | `ecc:tdd-guide,ecc:mle-reviewer,ecc:python-reviewer` |
| 23 | Obstacle detection + gap profiling + steering law | P2 | impl | `ecc:tdd-guide,ecc:silent-failure-hunter,ecc:python-reviewer` |
| 24 | Phone steer consumer: integrator, nav blend, decay | P3 | design, impl | `ecc:architect,ecc:tdd-guide,ecc:swift-reviewer` |
| 25 | Live X4 steering validation + pitch numbers | all | test, docs | `ecc:performance-optimizer,ecc:doc-updater` |
| 26 | Voice layer sees the gap profile | P2 | impl | `ecc:tdd-guide,ecc:python-reviewer` |
| 27 | Two-parameter affine scale fit (unblocks `proximityFactor`) | P2 | impl | `ecc:tdd-guide,ecc:mle-reviewer,ecc:python-reviewer` |
| 28 | 🔶 Navigation cue names the passable side (phone) | P3 | impl | `ecc:tdd-guide,ecc:swift-reviewer` |
| 29 | Unanchored ring restricted to the front crop | P2 | impl | `ecc:tdd-guide,ecc:python-reviewer` |

**Steps 21–26 (added 2026-09-26)** port the sensing & steering half of
[tonywangs/shepherd](https://github.com/tonywangs/shepherd#sensing--steering-pipeline) onto our
hardware: their LiDAR becomes Depth Anything V2 on the X4's forward crops, their ESP32 motor
becomes the phone's stereo/haptic cue. The binding spec is `docs/shepherd-steering-plan.md`;
21 gates 22–24, and 25 is a physical walk, not agent work.

**Parallelism.** Steps 1–3 are shared and gate everything. After that the three lanes run
independently — P1 takes 4–5, P2 takes 6–8 and 15–16, P3 takes 9–14 — meeting again at 19.
P3 starts on hour 1 against the golden fixture with no camera, no laptop and no cloud, which is
the only reason this fits in 36 hours with three people.

---

## Step 1 — Pre-flight spikes, keys, SDK application

**Intent:** Verify the hardware assumptions before the clock starts. Spike 1 is the single
highest-risk unknown in the project and spike 8 has a multi-day lead time.
**Chain rationale:** `planner` sequences and assigns the spike list; `docs-lookup` pulls current
Insta360, TensorRT and GCP quota documentation. No code is written, so no reviewer tail.

```bash
/everything-claude-code:orchestrate custom "ecc:planner,ecc:docs-lookup" "[Plan: docs/camera-sync-design.md#step-1] Run the §6 spike list before hour 0, in priority order: spike 1 (does the X4 enumerate as a UVC capture device to OpenCV on macOS — highest risk, the official manual routes through OBS), spike 8 (submit the Insta360 SDK application, ~3 business days), spike 7 (TensorRT engine builds and loads on the target L4), spike 4 (measure pipeline delay D with the screen-timer method), spike 9 (YOLO + DepthAnythingV2SmallF16 together against the <100 ms budget), spike 5 (hotspot RTT at the venue, pick a rung). Enable GCP, Maps, Gemini, Firebase and NVIDIA keys. Acceptance: every §6 row has an owner and a recorded result; spike 1 has a written go/no-go; D recorded as median plus spread"
```

---

## Step 2 — Event contract + golden fixtures

**Intent:** Build the two shared artifacts that decouple all three lanes. Everything else is
independent of everything else through this file.
**Chain rationale:** `architect` owns the schema shape since it is the one decision that is
expensive to change at hour 30; `tdd-guide` writes the fixture suite first; both language
reviewers close because the contract exists in Python and Swift simultaneously.

```bash
/everything-claude-code:orchestrate custom "ecc:architect,ecc:tdd-guide,ecc:python-reviewer,ecc:swift-reviewer" "[Plan: docs/camera-sync-design.md#step-2] Build the integration surface: contracts/awareness_event.schema.json with the 11 fields of §4.6, the closed class enum in one shared file, a Pydantic model for the backend, a Codable struct for Swift, and a golden fixture of ~10 valid and invalid events both test suites run against. urgency is NOT on the wire; range_m is never populated by the X4. Acceptance: both suites pass the same fixture; an invalid event is rejected rather than crashed on; a schema version field is present. Out of scope: any detector or feedback logic"
```

---

## Step 3 — NTP clock handshake + WebSocket transport

**Intent:** One shared clock, so stale warnings get dropped instead of announced late.
**Chain rationale:** Cross-language by nature — the server is Python and the client is Swift, so
both reviewers are required. `tdd-guide` leads because offset math is exactly the kind of thing
that looks right and is wrong.

```bash
/everything-claude-code:orchestrate custom "ecc:tdd-guide,ecc:python-reviewer,ecc:swift-reviewer" "[Plan: docs/camera-sync-design.md#step-3] Implement the §4.2 NTP handshake over the WebSocket: laptop PING(t1) then phone PONG(t2,t3), offset=((t2-t1)+(t3-t4))/2, rtt=(t4-t1)-(t3-t2), every 5 s, keeping the last 9 samples and using the LOWEST-RTT sample, not the median of the offsets. Apply the measured pipeline constant D on the laptop so t_capture_ms lands in phone-clock terms. Python WebSocket server, Swift URLSessionWebSocketTask client. Acceptance: offset stable within ±10 ms over hotspot LAN; a simulated clock jump self-corrects within 10 s; events round-trip end to end"
```

---

## Step 4 — Phone capture + intrinsics FOV + YOLO + bearing

**Intent:** P1's vertical slice without depth — get real events flowing from the phone first.
**Chain rationale:** This is the critical path with a hard <100 ms budget, so
`performance-optimizer` sits mid-chain to profile before `swift-reviewer` closes on correctness.

```bash
/everything-claude-code:orchestrate custom "ecc:tdd-guide,ecc:performance-optimizer,ecc:swift-reviewer" "[Plan: docs/device-responsibilities.md#step-4] P1 lane: AVCaptureSession wide-camera capture, landscape, no ARKit and no LiDAR. Enable isCameraIntrinsicMatrixDeliveryEnabled and derive halfFOV=atan(cx/fx) at startup — do not hardcode ±37°. Run YOLO CoreML every frame, map box centre column to torso-relative bearing_deg, and emit AwarenessEvent(source=\"phone\") with range_m nil. Acceptance: measured frame-to-event latency under 70 ms with YOLO only; FOV is derived not hardcoded; events validate against the golden fixture schema. Out of scope: depth, range, and anything outside ±37°"
```

---

## Step 5 — Depth Anything V2 + ground-plane scale anchoring

**Intent:** Turn relative depth into metres, and know when to stop claiming them.
**Chain rationale:** `mle-reviewer` covers the model-integration specifics — interleaving,
aspect handling, the scale-anchor health gate — that a general Swift reviewer would miss.

```bash
/everything-claude-code:orchestrate custom "ecc:tdd-guide,ecc:mle-reviewer,ecc:swift-reviewer" "[Plan: docs/camera-sync-design.md#step-5] Add DepthAnythingV2SmallF16 (only F32 and F16 ship — there is no INT8 variant) interleaved at 5–10 Hz so it never contends with YOLO on the ANE. Input is 518×396 and the frame is stretched to it, so correct the aspect before sampling. Take median depth inside each box; fit the ground plane in the lower region and scale so camera height matches the measured ~1.3 m; cross-check against cam_height/tan(angle_to_box_bottom) on person boxes and log the scale factor. Acceptance: a YOLO+depth frame stays under 100 ms; on divergence the phone stops stating metres and emits range_band instead"
```

---

## Step 6 — X4 latest-frame grab + 6-crop dewarp + sender

**Intent:** The bridge. Both of its rules fail silently, which is what makes this step dangerous.
**Chain rationale:** `performance-optimizer` because frame age at send is the entire deliverable —
correctness here is a latency property, not a functional one.

```bash
/everything-claude-code:orchestrate custom "ecc:tdd-guide,ecc:performance-optimizer,ecc:python-reviewer" "[Plan: docs/camera-sync-design.md#step-6] P2 lane bridge: grab the X4 stitched 2880×1440 equirect over UVC on a dedicated thread that keeps only the LATEST frame in one slot, never a queue, because CAP_PROP_BUFFERSIZE=1 is frequently ignored on macOS AVFoundation. Dewarp six 60° perspective crops via a precomputed OpenCV remap LUT, encode JPEG q60–70, and send with ONE REQUEST IN FLIGHT EVER — if a request is still pending when the next frame is due, drop that frame. Acceptance: measured frame age at send under 50 ms while the uplink is throttled; latency stays bounded under induced network delay"
```

---

## Step 7 — Cloud L4 TensorRT + ByteTrack + closing speed

**Intent:** The NVIDIA centrepiece, and the source of every `track_id` the feedback manager relies on.
**Chain rationale:** `architect` first because engine-build-on-target-GPU is a container and
deployment decision, not a coding one; `mle-reviewer` covers the tracker and inference path.

```bash
/everything-claude-code:orchestrate custom "ecc:architect,ecc:tdd-guide,ecc:mle-reviewer,ecc:python-reviewer" "[Plan: docs/camera-sync-design.md#step-7] Stand up the L4 inference service in us-east1: FastAPI, TensorRT YOLO, ByteTrack for stable track_ids, bearing rate and box growth to closing_mps, box height to range_band. Build the TensorRT engine ON the target L4 inside the container build — engines are not portable across GPU architectures. The cloud is timeless: echo t_capture and never read the local clock. Acceptance: the engine loads on a fresh L4 boot; inference under 25 ms; track_id stable across 30 frames; no metres ever emitted. Out of scope: urgency, OCR, and face work"
```

---

## Step 8 — Health monitor, rung stepping, CoreML fallback

**Intent:** Survive a saturated venue uplink without anyone touching a keyboard.
**Chain rationale:** `silent-failure-hunter` is the right specialist here — the whole step is about
failures that produce no error, only quietly late warnings.

```bash
/everything-claude-code:orchestrate custom "ecc:tdd-guide,ecc:silent-failure-hunter,ecc:python-reviewer" "[Plan: docs/camera-sync-design.md#step-8] Implement §3.4 automatic fallback behind one Detector interface. Step down a rung at p95 RTT over 500 ms across the last 10 frames; flip to local CoreML on 3 consecutive timeouts, or rung 4 still failing, or the feedback manager's stale-drop rate crossing threshold; step back up one rung after 30 s healthy. Silent and logged, never a keystroke. Four rungs: 6×640² q70 at 3 fps, the same at 2 fps, 6×512² q60 at 3 fps, the same at 2 fps. Acceptance: induced timeouts flip to local within 3 frames; recovery steps up at most once per 30 s; user-visible output is unchanged across the switch"
```

---

## Step 9 — Feedback manager gauntlet

**Intent:** The consumer, and the most demo-visible code in the project. Starts hour 1.
**Chain rationale:** `architect` because gauntlet *order* is the design — dedup before refractory
before urgency is load-bearing and hard to re-sequence later. Deliberately no
`silent-failure-hunter`: this component drops events by design, so it would flag intended behaviour.

```bash
/everything-claude-code:orchestrate custom "ecc:architect,ecc:tdd-guide,ecc:swift-reviewer" "[Plan: docs/camera-sync-design.md#step-9] P3 lane, hour 1, built against the golden fixture with no camera and no cloud. Implement the §4.7 gauntlet in order: validate, staleness gate (now minus t_capture over ttl means drop and count), dedup (within 10° and 300 ms from a different source, preferring the phone when it has healthy metric range inside 15 m), refractory suppression keyed on track_id, on-device urgency, then a priority queue with preemption. TTLs come from §4.4. Acceptance: the fixture suite passes including malformed events; a flood of one track_id yields one announcement; urgency 1 produces haptic only and never speech"
```

---

## Step 10 — Haptics + TTS + stereo panning output

**Intent:** The only channel the user actually experiences.
**Chain rationale:** `a11y-architect` leads because this *is* the accessibility surface — for a
blind user the haptic vocabulary is the interface, not a presentation detail.

```bash
/everything-claude-code:orchestrate custom "ecc:a11y-architect,ecc:tdd-guide,ecc:swift-reviewer" "[Plan: docs/device-responsibilities.md#step-10] Build the output layer: CoreHaptics directional patterns keyed to bearing, AVSpeechSynthesizer TTS, and AVAudioEngine L/R stereo panning, since AVAudioEnvironmentNode is cut. Announcements round to \"about two metres\", never \"2.3 metres\", and urgency thresholds use bands with hysteresis so a noisy estimate cannot flip urgency back and forth. Acceptance: a blindfolded listener identifies left, right and ahead from haptics alone well above chance; urgency 3 preempts an in-progress announcement; urgency 1 never speaks"
```

---

## Step 11 — Voice intent recognition

**Intent:** The control surface. Fixed intent set, on-device, no LLM in the loop.
**Chain rationale:** Small, self-contained Swift work — `tdd-guide` plus the language reviewer is
the whole requirement.

```bash
/everything-claude-code:orchestrate custom "ecc:tdd-guide,ecc:swift-reviewer" "[Plan: docs/device-responsibilities.md#step-11] Implement voice intent recognition with on-device SFSpeechRecognizer over a fixed MVP intent set: take me to X, what's around me, what's in front of me, read that sign. Use keyword and grammar matching rather than an LLM call, because Tier 3 latency buys nothing here. Resolve the microphone-source conflict recorded in build-plan.md §6 before wiring AVAudioSession, since .allowBluetooth changes the session category and affects TTS routing. Acceptance: each intent is recognized 5 times out of 5 in a quiet room and 4 out of 5 with street noise playing"
```

---

## Step 12 — Navigation: Routes API + turn cues

**Intent:** Turn-by-turn, kept strictly out of the awareness path.
**Chain rationale:** `docs-lookup` first because Routes API request shapes are easy to get subtly
wrong from memory and a wrong field costs an hour.

```bash
/everything-claude-code:orchestrate custom "ecc:docs-lookup,ecc:tdd-guide,ecc:swift-reviewer" "[Plan: docs/device-responsibilities.md#step-12] Build the navigation engine: Google Routes API walking route, Geocoding for destination lookup, CoreLocation true heading, route-polyline follow at about 1 Hz, heading error mapped to a directional haptic turn cue, and step advance on proximity. CoreLocation feeds navigation ONLY and never the awareness path, where bearings stay torso-relative. Acceptance: a two-turn walking route plays correct turn cues on the demo route; losing GPS degrades navigation without affecting hazard warnings"
```

---

## Step 13 — Sign and text reading

**Intent:** Cheap, high-demo-value, permanently phone-only.
**Chain rationale:** Contained Vision-framework work; no specialist adds value over the language reviewer.

```bash
/everything-claude-code:orchestrate custom "ecc:tdd-guide,ecc:swift-reviewer" "[Plan: docs/device-responsibilities.md#step-13] Add voice-triggered sign and text reading with Vision VNRecognizeTextRequest in accurate mode on a full rectilinear phone frame, filtered by box area and confidence, reading the largest plausible result aloud. This stays phone-only permanently, because OCR needs a rectilinear view and fisheye crops would wreck it. Acceptance: reads a room number and a building name correctly from about 3 m on the demo route; returns silence rather than garbage when no text is present"
```

---

## Step 14 — VLM scene description, front + around

**Intent:** Both description paths, with the projection rule that keeps the X4 one usable.
**Chain rationale:** `docs-lookup` because every PDF-era Gemini model ID is dead;
`security-reviewer` closes because this is the first step that ships camera frames off-device.

```bash
/everything-claude-code:orchestrate custom "ecc:docs-lookup,ecc:tdd-guide,ecc:swift-reviewer,ecc:security-reviewer" "[Plan: docs/device-responsibilities.md#step-14] Add both scene-description paths using gemini-3.8-flash, since the PDF-era model IDs are all dead — see build-plan.md §1.2. \"In front of me\" sends one rectilinear phone frame. \"Around me\" sends 2 to 4 dewarped 60° crops and NEVER the raw equirect, because VLMs are not trained on equirectangular projection and will describe it badly. TTL 15 s. Acceptance: both paths return a usable description within 2 s; the API key is not hardcoded; the feature announces unavailability on network failure instead of hanging"
```

---

## Step 15 — Hazard map Firestore writes

**Intent:** The data product, and the A2A agent's substrate.
**Chain rationale:** `security-reviewer` closes rather than `database-reviewer` leading — the
latter is PostgreSQL-specialized and this is Firestore, where the real risk is the security rules,
not the schema.

```bash
/everything-claude-code:orchestrate custom "ecc:tdd-guide,ecc:python-reviewer,ecc:security-reviewer" "[Plan: docs/device-responsibilities.md#step-15] Write detected static hazards to Firestore via firebase-admin: geohash the GPS fix, deduplicate by geohash cell plus class, and keep it write-only for MVP. Lock the Firestore security rules, because an open write path on a public hazard collection is the obvious vulnerability here. Acceptance: a planted hazard appears in Firestore within 5 s; a second sighting in the same cell updates rather than duplicates; unauthenticated writes from outside the app are rejected"
```

---

## Step 16 — Latency instrumentation + benchmark slide

**Intent:** The measurement that turns an asserted claim into a proven one. Nearly free.
**Chain rationale:** `performance-optimizer` shapes what gets measured so the distribution is
actually comparable across detectors rather than merely plotted.

```bash
/everything-claude-code:orchestrate custom "ecc:tdd-guide,ecc:performance-optimizer,ecc:python-reviewer" "[Plan: docs/camera-sync-design.md#step-16] Turn §4.9's free benchmark data into the latency slide: the phone logs now minus t_capture at announcement time per class and per source, exports CSV, and a pandas and matplotlib script produces the distribution plot. This is what proves TensorRT versus CoreML rather than asserting it. Acceptance: a 10-minute walk yields a per-class p50 and p95 table; cloud and local detector rows are directly comparable on the same route"
```

---

## Step 17 — 🔶 Last-50-feet navigation

**Intent:** The Waymo-track differentiator. Swing item — only after the core route demo is solid.
**Chain rationale:** Three Maps APIs whose request shapes must be right first time.

```bash
/everything-claude-code:orchestrate custom "ecc:docs-lookup,ecc:tdd-guide,ecc:swift-reviewer" "[Plan: smart-cane-shellhacks-plan.md#step-17] Swing item. Last-50-feet navigation: the Places API (New) resolves the entrance lat and lng, the Street View Static API fetches a reference image of the door, and the phone's vision path then guides the final approach. Start only after the core route demo is solid. Acceptance: on the demo route the system guides to the actual door rather than the building centroid; it degrades to ordinary navigation when Places has no entrance data"
```

---

## Step 18 — 🔶 ADK Workflow orchestration + A2A agent

**Intent:** The Google Cloud track. Go/no-go at hour 20 — decide the deadline now, not at hour 30.
**Chain rationale:** `docs-lookup` is mandatory, not optional: the PDF's entire agent design
targets an API that is now legacy, and one of its mechanisms does not work at all.

```bash
/everything-claude-code:orchestrate custom "ecc:docs-lookup,ecc:architect,ecc:tdd-guide,ecc:python-reviewer" "[Plan: smart-cane-shellhacks-plan.md#step-18] Swing item, go/no-go at hour 20. Build the 1 to 2 Hz awareness loop on ADK's CURRENT Workflow API — LoopAgent and ParallelAgent are legacy, and SequentialAgent never honours escalate, so loop exit must be an explicit edge condition; see build-plan.md §1.1. Fan out Hazard Scout, Traffic Predictor and Sign Reader via @node(parallel_worker=True), fan in with JoinNode, and have the synthesizer emit one announcement or stay silent. Wrap the TensorRT detector as a Hazard Scout tool and add the a2a-sdk hazard-map agent. Acceptance: it stays outside the safety path; sequential-versus-parallel numbers are recorded"
```

---

## Step 19 — Integration, demo route, freeze, pitch

**Intent:** Hours 24–36. One flawless route beats ten half-working features.
**Chain rationale:** `planner` sequences the freeze and the cut list; `doc-updater` reconciles the
docs with what actually shipped. No `e2e-runner` — it is Playwright-shaped and this is a physical
walking demo.

```bash
/everything-claude-code:orchestrate custom "ecc:planner,ecc:doc-updater" "[Plan: smart-cane-shellhacks-plan.md#step-19] Hours 24 to 36: walk the demo route blindfolded repeatedly, fix what breaks, cut what is flaky, then feature-freeze. Record the backup demo video, finish the deck with the measured latency table plus the ethics and privacy points, rehearse the pitch, and run the warm-up ping loop so the demo is never the cloud's first request. Acceptance: three consecutive clean blindfolded runs of the full route; the backup video is recorded; every doc in docs/ matches what actually shipped"
```

---

## Batch execution

Paste in order. Steps 4–5, 6–8 and 9–14 belong to different people and can run concurrently.

```bash
# Shared foundation — gates everything
/everything-claude-code:orchestrate custom "ecc:planner,ecc:docs-lookup" "[Plan: docs/camera-sync-design.md#step-1] ..."
/everything-claude-code:orchestrate custom "ecc:architect,ecc:tdd-guide,ecc:python-reviewer,ecc:swift-reviewer" "[Plan: docs/camera-sync-design.md#step-2] ..."
/everything-claude-code:orchestrate custom "ecc:tdd-guide,ecc:python-reviewer,ecc:swift-reviewer" "[Plan: docs/camera-sync-design.md#step-3] ..."

# P1 — perception (iOS), critical path
/everything-claude-code:orchestrate custom "ecc:tdd-guide,ecc:performance-optimizer,ecc:swift-reviewer" "[Plan: docs/device-responsibilities.md#step-4] ..."
/everything-claude-code:orchestrate custom "ecc:tdd-guide,ecc:mle-reviewer,ecc:swift-reviewer" "[Plan: docs/camera-sync-design.md#step-5] ..."

# P2 — X4 bridge + cloud
/everything-claude-code:orchestrate custom "ecc:tdd-guide,ecc:performance-optimizer,ecc:python-reviewer" "[Plan: docs/camera-sync-design.md#step-6] ..."
/everything-claude-code:orchestrate custom "ecc:architect,ecc:tdd-guide,ecc:mle-reviewer,ecc:python-reviewer" "[Plan: docs/camera-sync-design.md#step-7] ..."
/everything-claude-code:orchestrate custom "ecc:tdd-guide,ecc:silent-failure-hunter,ecc:python-reviewer" "[Plan: docs/camera-sync-design.md#step-8] ..."
/everything-claude-code:orchestrate custom "ecc:tdd-guide,ecc:python-reviewer,ecc:security-reviewer" "[Plan: docs/device-responsibilities.md#step-15] ..."
/everything-claude-code:orchestrate custom "ecc:tdd-guide,ecc:performance-optimizer,ecc:python-reviewer" "[Plan: docs/camera-sync-design.md#step-16] ..."

# P3 — experience (iOS) + navigation
/everything-claude-code:orchestrate custom "ecc:architect,ecc:tdd-guide,ecc:swift-reviewer" "[Plan: docs/camera-sync-design.md#step-9] ..."
/everything-claude-code:orchestrate custom "ecc:a11y-architect,ecc:tdd-guide,ecc:swift-reviewer" "[Plan: docs/device-responsibilities.md#step-10] ..."
/everything-claude-code:orchestrate custom "ecc:tdd-guide,ecc:swift-reviewer" "[Plan: docs/device-responsibilities.md#step-11] ..."
/everything-claude-code:orchestrate custom "ecc:docs-lookup,ecc:tdd-guide,ecc:swift-reviewer" "[Plan: docs/device-responsibilities.md#step-12] ..."
/everything-claude-code:orchestrate custom "ecc:tdd-guide,ecc:swift-reviewer" "[Plan: docs/device-responsibilities.md#step-13] ..."
/everything-claude-code:orchestrate custom "ecc:docs-lookup,ecc:tdd-guide,ecc:swift-reviewer,ecc:security-reviewer" "[Plan: docs/device-responsibilities.md#step-14] ..."

# Swing — only after the route demo is solid
/everything-claude-code:orchestrate custom "ecc:docs-lookup,ecc:tdd-guide,ecc:swift-reviewer" "[Plan: smart-cane-shellhacks-plan.md#step-17] ..."
/everything-claude-code:orchestrate custom "ecc:docs-lookup,ecc:architect,ecc:tdd-guide,ecc:python-reviewer" "[Plan: smart-cane-shellhacks-plan.md#step-18] ..."

# Freeze
/everything-claude-code:orchestrate custom "ecc:planner,ecc:doc-updater" "[Plan: smart-cane-shellhacks-plan.md#step-19] ..."
```

> The batch block abbreviates each task description to `...` for readability. Copy the **full**
> command from that step's own section above — the task text is what makes the chain self-contained.
