# Consolidated Build Plan — features, hardware, compute, APIs, services

**Status:** Derived doc · **Last updated:** 2026-09-26
**Sources:** `smart-cane-shellhacks-plan.md (1).pdf` (master) · `camera-sync-design.md` (§0–§4 authoritative) · `device-responsibilities.md` (ownership)
**Companion:** `orchestration-runbook.md` — the 19 build steps and their agent chains.

> This doc answers one question per feature: *what does it take to build it?*
> Hardware · libraries · what actually gets computed and where · APIs · external and cloud services.
> Where the source docs disagree or are out of date, §1 says so and this doc wins.

---

## 1. Corrections to the source docs (verified 2026-09-26)

Four load-bearing facts have moved since the docs were written. Each changes work.

### 1.1 ADK's `LoopAgent` / `ParallelAgent` / `SequentialAgent` are now the **legacy** API

The PDF's entire §4 "ADK agent design" is written against an API that `google/adk-python` now
files under `contributing/samples/legacy_workflows/`. The current surface is `Workflow`:

```python
from google.adk import Workflow
from google.adk.workflow import node, JoinNode, START

@node(parallel_worker=True)          # replaces ParallelAgent fan-out
async def hazard_scout(node_input: str) -> str: ...

join = JoinNode(name="join_perception")   # fan-in, aggregates by task name

root_agent = Workflow(
    name="awareness_loop",
    edges=[(START, (hazard_scout, traffic_predictor, sign_reader), join, synthesizer)],
)
```

**Second, sharper problem:** `SequentialAgent` never reads `ctx.end_invocation` or
`event.actions.escalate`. The PDF's *"Escalates to exit on arrival or cancel"* **does not work as
written** — escalation does not stop sibling execution. Loop termination has to be an explicit
edge condition in the `Workflow` graph.

`output_key` survives, and gains a useful trick: `output_key="temp:draft"` hands a result to the
next node **without persisting it** — exactly right for per-frame perception output that should
never accumulate in session state.

→ Affects step 18. Budget the ADK swing item against the `Workflow` API, not the PDF's.

### 1.2 Gemini model IDs have all changed

Every model name in the PDF era is dead. Current IDs:

| Use | Model ID | Note |
|---|---|---|
| Scene description (vision) | `gemini-3.8-flash` | GA 2026-09-26. 1,048,576 in / 65,536 out |
| Cheap/fast vision | `gemini-3.5-flash-lite` | for the 15 s-TTL "around me" path |
| Live bidi voice | `gemini-3.8-live` | low-latency voice agent variant |
| Live + reasoning | `gemini-3.8-live-extended-thinking` | priced differently |
| STT | `gemini-3.5-transcribe` / `-live` | if not using on-device `SFSpeechRecognizer` |
| TTS | `gemini-3.8-flash-tts` / `-lite-tts` | if not using `AVSpeechSynthesizer` |

All Gemini 3.x accept image input. → Affects steps 14 and 18.

### 1.3 `apple/coreml-depth-anything-v2-small` has **no INT8 variant**

`camera-sync-design.md` §2.1 says "F16, F16INT8 and F32 variants available." The repo ships
**two**: `DepthAnythingV2SmallF32` (99.2 MB) and `DepthAnythingV2SmallF16` (49.8 MB). Plan on F16.

- **Input is 518×396**, not square — the frame gets stretched to that, so bake the aspect
  correction into the box→depth sampling or ranges will be wrong off-centre.
- **Published latency: 31.1 ms (iPhone 12 Pro Max), 33.9 ms (iPhone 15 Pro Max)**, both F16.
  The sync doc budgets 15–20 ms. That is optimistic by roughly 2×.

### 1.4 The Tier-1 budget is tighter than §2.1 claims

Re-running the budget with the measured depth number:

| step | sync doc | corrected |
|---|---|---|
| AVCapture → pixel buffer | 30–50 ms | 30–50 ms |
| YOLO (n/s @ 640) | ~15 ms | ~15 ms |
| Depth Anything V2 Small F16 | ~15–20 ms | **~25–34 ms** |
| box sampling + plane fit | ~2 ms | ~2 ms |
| **YOLO-only frame** | — | **~47–67 ms** ✅ |
| **YOLO + depth frame** | ~70–90 ms | **~72–101 ms** ⚠️ at the limit |

Interleaving depth at 5–10 Hz stops being "the main lever if the budget gets tight" and becomes
**required on day one**. Spike 9 measures this first, not eventually.

---

## 2. Feature → requirements matrix

Ownership (P1 perception-iOS · P2 X4/cloud · P3 experience-iOS) per `device-responsibilities.md`.

---

### F1 · Front hazard detection + metric range — MVP, P1, critical path

The collision zone. ±37°, to ~15 m, on-device, never leaves the phone.

- **Hardware** — iPhone (wide camera), chest strap mount landscape −15° at ~1.3 m · Neural Engine
- **Libraries** — `AVFoundation`/`AVCaptureSession` · `CoreML` · `Vision` · Ultralytics YOLO
  exported to CoreML (n or s @ 640) · `DepthAnythingV2SmallF16.mlpackage` · `Accelerate`/`simd`
  for the plane fit. **No ARKit, no LiDAR** (sync doc §2.1)
- **Computation** — YOLO every frame → boxes+classes; Depth Anything V2 interleaved at 5–10 Hz →
  relative depth; ground-plane fit in the lower depth region → scale factor from known camera
  height; median depth inside each box → `range_m`; box centre column + intrinsics-derived FOV →
  `bearing_deg`. Budget §1.4. Cross-check metric range against `cam_height / tan(angle_to_box_bottom)`
  on person detections; on divergence **stop stating metres** and fall back to bands
- **APIs** — `AVCaptureConnection.isCameraIntrinsicMatrixDeliveryEnabled` +
  `kCMSampleBufferAttachmentKey_CameraIntrinsicMatrix` → `halfFOV = atan(cx/fx)`. Never hardcode ±37°
- **Cloud / external** — **none.** This is the whole point: design principle #1

---

### F2 · Side / rear / front-quarter awareness — MVP, P2

Everything outside ±30°. Existence, bearing, track, closing speed — **never metres**.

- **Hardware** — Insta360 X4 on a shoulder mount, lens axes front/back (seams at ±90°) ·
  microSD V30+ 64 GB · short USB-C chest→backpack · MacBook Pro M2 Pro in a backpack ·
  cloud NVIDIA L4 in `us-east1` · phone hotspot
- **Libraries** — laptop: `opencv-python` (UVC grab + precomputed `remap` LUT), `numpy`, `httpx`,
  `websockets`/`fastapi`. Cloud: `FastAPI` + `Uvicorn`, PyTorch + **TensorRT**, Ultralytics YOLO,
  ByteTrack, `Docker`. Fallback: CoreML on the M2 Pro behind the same `Detector` interface
- **Computation** — X4 emits stitched 2880×1440 equirect @30 fps → dedicated latest-frame grab
  thread (one slot, never a queue) → **6 × 60° perspective crops** via precomputed remap LUT
  (sub-ms) → JPEG q60–70 → **one request in flight, ever** → L4 TensorRT YOLO → ByteTrack →
  `track_id`, bearing rate, box growth → `closing_mps`, `range_band` from box height
- **APIs** — UVC / AVFoundation capture · internal HTTP POST to the inference service ·
  WebSocket to the phone
- **Cloud / external** — GCP Compute Engine GPU VM (L4), `us-east1` ≈ 20–30 ms RTT from Florida ·
  Docker · optional NVIDIA NIM (`build.nvidia.com`) for the hosted model layer
- **Two rules that both fail silently** — newest-frame-only grab; one-request-in-flight.
  Skip either and warnings arrive quietly late

---

### F3 · Clock handshake + event transport — MVP, P1 + P2

- **Hardware** — phone hotspot LAN (laptop ↔ phone)
- **Libraries** — Swift `URLSessionWebSocketTask` · Python `websockets` / FastAPI WebSocket
- **Computation** — NTP four-timestamp exchange every 5 s over the existing socket;
  `offset = ((t2−t1)+(t3−t4))/2`, `rtt = (t4−t1)−(t3−t2)`. Keep the last 9 samples and
  **use the lowest-RTT sample, not the median of the offsets**. Pins offset to ±10 ms over hotspot LAN.
  Pipeline delay `D` is a measured constant (screen-timer method, spike 4), applied on the laptop
- **APIs** — WebSocket only. Events route cloud → laptop → phone so there is one clock pair
- **Cloud / external** — none beyond the hotspot

---

### F4 · Feedback manager — MVP, P3

The consumer. Buildable on hour 1 from the golden fixture with no camera, laptop, or cloud.

- **Hardware** — phone only
- **Libraries** — pure Swift. `Codable` structs against `contracts/awareness_event.schema.json`
- **Computation** — every event runs the gauntlet in order: **validate** (schema, enum,
  bearing ∈ (−180,180], ttl>0) → **staleness gate** (`now − t_capture > ttl` → drop, bump the
  counter) → **dedup** (within 10° and 300 ms from a different source; prefer the phone when it
  has healthy metric range inside 15 m) → **refractory suppression** (keyed on `track_id` — this
  is the anti-flooding mechanism) → **urgency** (on-device, never on the wire) → **priority queue**
  (one announcement at a time, higher urgency preempts). TTC = `range_m / closing_mps`; without
  metric range, `range_band` substitutes
- **TTLs** — cyclist/scooter 500 ms · car 400 ms · pedestrian 1.5 s · static 8 s · scene 15 s
- **Urgency** — 3 immediate (TTC<2 s): burst + one word, refractory 0 · 2 act-soon (TTC 2–5 s or
  static <2 m): directional haptic + 2–3 words, refractory 2 s · 1 aware: **haptic only, no
  speech**, refractory 8 s · 0 ambient: log + map, never announced
- **Cloud / external** — none. Drop counts feed the F8 fallback trigger: one mechanism, two jobs

---

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

### F6 · Voice control — MVP, P3

"Take me to X" · "What's around me" · "What's in front of me" · "Read that sign"

- **Hardware** — **iPhone microphone** (see §6 conflict #1) · Neural Engine
- **Libraries** — `Speech` / `SFSpeechRecognizer` (on-device mode) · `AVAudioSession`
- **Computation** — speech → text on-device → intent classification. Keyword/grammar matching is
  enough for a fixed MVP intent set; an LLM call adds 0.5–2 s for no demo benefit
- **APIs** — optional `gemini-3.8-live` for free-form conversational intents (Tier 3 only)
- **Cloud / external** — none for MVP; Gemini Live only if free-form is attempted
- **Trap** — `AVAudioSession`'s `.allowBluetooth` silently kills `CMHeadphoneMotionManager`.
  Head tracking is cut, so this is defused — but it also means AirPods-as-mic changes the session
  category, which matters for TTS routing

---

### F7 · Turn-by-turn navigation — MVP, P3

- **Hardware** — iPhone GPS + magnetometer
- **Libraries** — `CoreLocation` (`CLLocationManager`, true heading) · `URLSession` ·
  optionally Google Maps SDK for iOS for a judge-facing map view
- **Computation** — route polyline follow, heading error → directional haptic turn cue,
  step advance on proximity. Runs at ~1 Hz
- **APIs** — **Google Routes API** (`routes.googleapis.com`, walking mode) · Geocoding API for
  destination lookup
- **Cloud / external** — Google Maps Platform key with Routes + Geocoding enabled
- **Hard boundary** — CoreLocation feeds **navigation only**. It never touches the awareness path;
  bearings there are torso-relative precisely so `CLLocation.course` (unreliable below ~0.5 m/s —
  i.e. standing at a curb) can never define the reference frame

---

### F8 · Health monitor + automatic fallback — MVP, P2

- **Hardware** — M2 Pro (runs the local CoreML detector)
- **Libraries** — `coremltools` / CoreML via a Python bridge or a small Swift helper, behind the
  same `Detector` interface as the cloud path
- **Computation** — step **down** a rung when p95 RTT > 500 ms over the last 10 frames; flip to
  **local** on 3 consecutive timeouts, or already-on-rung-4-and-still-failing, or the F4
  stale-drop rate crossing threshold; recover one rung after 30 s healthy. **Silent and logged** —
  not a keystroke, because a hall with a thousand people on 5G is a genuinely saturated uplink
- **Rungs** — 1: 6×640² q70 @3 fps ≈ 6.5 Mbps · 2: @2 fps ≈ 4.3 · 3: 6×512² q60 @3 ≈ 2.3 ·
  4: 6×512² q60 @2 ≈ 1.5. Resolution *is* the detection-range knob (640/60° = 10.7 px/deg →
  a 1.8 m cyclist at 20 m = 55 px; 512 → 44 px)
- **Cloud / external** — none; this is the thing that survives the cloud being gone

---

### F9 · Sign and text reading — MVP, P3, cheap

- **Hardware** — iPhone wide camera
- **Libraries** — `Vision` / `VNRecognizeTextRequest` (accurate mode)
- **Computation** — on-demand only, voice-triggered. Full-frame OCR, filter by box area and
  confidence, read the largest plausible result
- **Cloud / external** — none
- **Why phone-only** — OCR needs a rectilinear view, and the *raw* equirect would wreck it.

**Amended 2026-09-27 (indoor sign wayfinding).** "Never" was wrong, and the reason is that the X4
path does not send the equirect anywhere: `bridge/dewarp.py` cuts it into six 60° **rectilinear**
crops before anything looks at it, which is exactly the view OCR wants. Reading signage off those
crops is now built (`scene/signscan.py`) and it is strictly better than the phone for this job,
because the wearer's own camera cannot see the plate they have already walked past. Step 13's
on-phone `VNRecognizeTextRequest` remains unbuilt and is no longer on the critical path

---

### F10 · Scene description "in front of me" — MVP, P3

- **Hardware** — iPhone camera · network
- **Libraries** — `URLSession` (or `google-genai` server-side if proxied)
- **Computation** — grab one rectilinear frame, JPEG, one VLM call, TTS the result.
  Tier 3: ~0.5–2 s, TTL 15 s, latency-tolerant by nature
- **APIs** — `gemini-3.8-flash` (or `gemini-3.5-flash-lite` for cost)
- **Cloud / external** — Gemini API (AI Studio key or Vertex AI)

---

### F11 · Scene description "around me" — MVP, P2 + P3

- **Hardware** — X4 → laptop → cloud
- **Libraries** — same, server-side `google-genai`
- **Computation** — **2–4 dewarped 60° crops**, never the raw equirect. VLMs are not trained on
  equirectangular projection and will describe it badly. Stitch the crop captions into one
  bearing-annotated sentence
- **APIs** — `gemini-3.8-flash`
- **Cloud / external** — Gemini API

---

### F12 · Crowdsourced hazard map — MVP write path, P2

- **Hardware** — cloud only
- **Libraries** — `firebase-admin` (Python)
- **Computation** — static-class detections with a stable `track_id` and a GPS fix → geohashed
  Firestore doc. Deduplicate by geohash cell + class. Write-only for MVP; read-back is the
  route-scoring stretch
- **APIs** — Firestore REST/admin SDK
- **Cloud / external** — Firebase project + Firestore. **Lock the security rules** — an open
  write path on a public hazard collection is the obvious vulnerability here

---

### F13 · Latency instrumentation + benchmark — MVP, P2

Nearly free, and it is a slide.

- **Computation** — every event carries `t_capture`, so the phone logs `now − t_capture` at
  announcement time. That is a **measured** end-to-end distribution, per class, on real walking
  data — and it is how TensorRT-vs-CoreML gets proven rather than asserted
- **Libraries** — Swift logging → CSV/JSON export · `pandas` + `matplotlib` for the slide
- **Cloud / external** — none

---

### F14 · Last-50-feet navigation — 🔶 swing, P3

- **Hardware** — iPhone
- **Libraries** — `URLSession`, `Vision`
- **Computation** — Places gives the entrance lat/lng, Street View Static gives a reference image
  of the door, then F1's vision path guides the final approach
- **APIs** — **Places API (New)** · **Street View Static API** · Elevation API (route scoring)
- **Cloud / external** — Google Maps Platform (same key, additional APIs enabled)

---

### F15 · ADK orchestration — 🔶 swing, P2 · **go/no-go at hour 20**

- **Libraries** — `google-adk` (**`Workflow` API — see §1.1, not `LoopAgent`**) · `google-genai`
- **Computation** — awareness loop at ~1–2 Hz, **outside the safety path**. Fan out
  Hazard Scout ∥ Traffic Predictor ∥ Sign Reader via `@node(parallel_worker=True)`, fan in with
  `JoinNode`, synthesizer picks the single most important announcement or stays silent.
  Loop exit must be an explicit edge condition — escalation does not stop siblings
- **The benchmark that sells it** — sequential vs. parallel on the same frame, both numbers on a slide
- **Cloud / external** — GCP, Gemini API
- **Track value** — the TensorRT detector wrapped as a tool called by Hazard Scout makes one
  component count for both the NVIDIA and Google Cloud tracks

---

### F16 · A2A hazard-map agent — 🔶 swing, P2

- **Libraries** — `a2a-sdk` (agent card + server)
- **Computation** — standalone agent over the F12 Firestore collection. The cane reports, other
  agents query. Demo with a second client — a mock city dashboard or a rideshare agent asking for
  an accessible pickup point
- **Cloud / external** — GCP, Firestore
- **Business angle** — the hazard map is a data product for cities (ADA compliance, sidewalk
  repair prioritization)

---

### Cut — and why (do not re-litigate at 3am)

| Feature | Why cut |
|---|---|
| Drop-off detection | Ground-plane discontinuity, monocular depth's weakest case. **The white cane already does this tactilely and is very good at it.** "We extend the cane, we don't replace it" |
| Consent-based face memory | Separate embedding model + encrypted store + enrollment flow + privacy story, for the least differentiated feature on the list. Biggest single scope saving |
| `AVAudioEnvironmentNode` spatial audio | Hours of work; L/R panning + haptics conveys direction nearly as well for a demo |
| LiDAR / ARKit | Monocular depth runs on **any** iPhone (strengthens the Google.org pitch) and is not capped at ~5 m |
| Apple Watch haptics, transit mode, crosswalk assist, "guide me to my ride", elevation scoring, fall detection | Scope |

---

## 3. Consolidated bill of materials

Reflects the chest/shoulder amendment — the PDF's cane-mount and hat-mount rows are superseded.

| Item | Qty | Notes |
|---|---|---|
| iPhone (17 Pro on hand) | 1 | Wide camera + Neural Engine. **LiDAR unused** — any modern iPhone works, which *is* the pitch |
| **Chest-strap phone mount** | 1 | **Primary.** Landscape, tilted 15–20° down, ~1.3 m |
| Insta360 X4 | 1 | Borrowed from UF Marston. Bring a charged spare battery |
| **Shoulder mount for the X4** | 1 | Lens axes front/back → seams at ±90° |
| microSD V30+ 64 GB | 1 | For the X4 |
| Short USB-C cable, chest → backpack | 1 | X4 → laptop |
| USB-C cables (C-C, C-A) | 3+ | Webcam mode, charging |
| MacBook Pro M2 Pro | 1 | Bridge laptop, in a backpack. Also runs the CoreML fallback |
| Mac with Xcode | 1+ | Required to build and sign the iOS app |
| White cane (folding) | 1 | Demo prop **and the tactile drop-off detector** |
| AirPods Pro | 1 | TTS out. **Head tracking not used** — see §6 conflict #1 for the mic |
| Power banks | 2 | Phone and X4 both drain fast while streaming |
| Phone with hotspot plan | 1 | Carries cloud uplink, events, **and** Tier 3 calls. Measure at the venue |
| Blindfold | 1 | Demo |
| Obstacle props (cone, box, chair) | 2–3 | Planted hazards |
| RTX laptop | 0–1 | Open question — see §6 conflict #2 |
| ~~Cane phone clamp~~, ~~cap/hat mount~~, ~~Apple Watch~~ | — | Superseded / cut |

---

## 4. Accounts, keys, and cloud services

Set up **before** the clock starts. Two of these have multi-day lead times.

| Service | What to enable | Lead time |
|---|---|---|
| **Insta360 SDK application** | Submit today — the production phone-direct path | **~3 business days** ⚠️ |
| GCP project | Billing or sponsor credits | Same day |
| Compute Engine GPU VM | **NVIDIA L4, `us-east1`** (South Carolina, ~20–30 ms from Florida) | Quota may need a request ⚠️ |
| Google Maps Platform | Routes · Places (New) · Street View Static · Elevation · Geocoding | Same day |
| Gemini API | AI Studio key or Vertex AI. Model IDs per §1.2 | Same day |
| Firebase | Firestore for the hazard map + **security rules** | Same day |
| NVIDIA | `build.nvidia.com` API key for NIM (optional) | Same day |
| Apple ID | Device signing. **Free-account builds expire after 7 days** | Same day |
| Docker + Artifact Registry | Backend container. **Build the TensorRT engine on the L4 itself** | — |
| ngrok / Cloudflare Tunnel | Only if the backend runs on a laptop | Same day |
| GitHub repo + Figma | Code + architecture diagram | Same day |

**Two cloud gotchas that will bite on stage:**

1. **A TensorRT engine is not portable across GPU architectures.** Build it *on* the L4 you
   deploy to, not on a dev box, or it will not load. Bake this into the container build.
2. **Cold start is 30–60 s+.** Never let the demo be the first request — run a warm-up ping loop
   from the moment you walk toward the stage.

---

## 5. Compute placement ledger

| Where | Runs | Latency | Fails how |
|---|---|---|---|
| **iPhone ANE** | YOLO every frame · Depth Anything V2 at 5–10 Hz · OCR on demand · STT | **<100 ms** (§1.4) | Never — no network in this path |
| **iPhone CPU** | Feedback manager · haptics · TTS · nav follower | ~30 ms | Never |
| **M2 Pro laptop** | UVC grab · 6-crop remap LUT · JPEG · sender · health monitor · CoreML fallback | ~8–12 ms + I/O | Degrades to fallback detector |
| **Cloud L4** | TensorRT YOLO · ByteTrack · closing speed | 12–25 ms inference, **175–300 ms typical end-to-end**, p95 400–600 ms | Health monitor flips to local, silently |
| **Gemini** | Scene description · optional voice | 0.5–2 s | Feature announces unavailable |
| **Firestore** | Hazard map writes | async | Fire-and-forget |

A cyclist at 15 mph covers ~1 m during the extra typical cloud latency and ~3.4 m in the p95 tail.
Detected at 15–20 m, the warning still lands ~2 s before they pass. **This is acceptable only
because imminent hazards never leave the device.**

---

## 6. Conflicts and open questions needing a team decision

**Conflict #1 — which microphone? RESOLVED 2026-09-26: the iPhone's own microphone.**
`device-responsibilities.md` was right; `camera-sync-design.md` §5's "the AirPods mic is fine for
voice input" is superseded and annotated there. Binding configuration is `step-10-design.md`
Amendment 5: `.playAndRecord` + `.spokenAudio` with
`[.duckOthers, .allowBluetoothA2DP, .allowAirPlay, .defaultToSpeaker]` — **never `.allowBluetooth`**.

*Why this way round:* taking mic input from the AirPods forces the HFP profile, which drops output
to call-grade audio for the whole walk. Keeping the mic on the phone leaves output on A2DP, so
speech stays high quality through AirPods when they are worn, and `.defaultToSpeaker` sends it to
the phone's own speaker when they are not. One ruling buys good audio in both cases.

**Conflict #2 — does the RTX laptop have a role?** A local TensorRT bridge cuts the uplink hop
entirely but loses the cloud story and the measured cloud-vs-local benchmark. Only relevant if
someone actually owns that hardware.

**Open, from the source docs:**

1. **Who is P1?** Critical path; needs the strongest Swift dev.
2. **ADK go/no-go at hour 20** — decide the deadline now, not at hour 30.
3. **Which YOLO weights, and do phone and cloud share a class list?** They **must**, to satisfy the
   closed enum in the contract. Decide before step 2 freezes the schema.
4. Is cutting face memory agreed?
5. Stereo panning instead of `AVAudioEnvironmentNode`?
6. NVIDIA track framing now that TensorRT sits in the cloud rather than on the bridge laptop.

**Closed class enum — both sides must agree, lives in one file:**

```
moving:  person · cyclist · scooter · car · bus
static:  pole · bollard · wall · parked_car · door · overhang
catch:   unknown_obstacle
```

`unknown_obstacle` matters more than it looks: detectors find shapes they cannot label, and those
still need to reach the user.
