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
