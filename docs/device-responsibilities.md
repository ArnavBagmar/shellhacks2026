# Device Responsibilities — iPhone vs Insta360 X4

**Status:** Draft for team review · **Team size: 3**
**Last updated:** 2026-09-26
**Companion doc:** `camera-sync-design.md` — the frames, clock, and event contract this doc divides work across.

> This doc answers: what does the phone do, what does the X4 do, who owns which feature,
> and what got cut. If you are about to build something, check it appears here first.

---

## The integration surface is two files

Everything below is decoupled by exactly **two** shared artifacts:

1. `contracts/awareness_event.schema.json` — the 11-field event contract
2. The NTP ping/pong message pair on the WebSocket

That's it. No shared state, no shared models, no shared frames. **Both camera pipelines are
independent producers of `AwarenessEvent`s**, and the feedback manager is a pure consumer.

Which means P3 can build and test the entire feedback manager from a fixture file **with no
camera, no laptop, and no cloud** — and that parallelism is the only reason this fits in 36 hours
with three people.

---

## Coverage map

```
 signed bearing (torso-relative, 0° = chest forward, + = right)

 -180°    -150°    -90°     -30°  0°  +30°     +90°     +150°   +180°
   │        │        │        │   │   │         │         │        │
   │  C4    │   C5   │        │ C0│   │   C1    │   C2    │   C3   │
   ├────────┼────────┼────────┴───┴───┴─────────┼─────────┼────────┤
   │        │        │                          │         │        │
   │              X4: six 60° crops from the stitched equirect      │
   │        │        │                          │         │        │
   └────────┴────────┴──────────┬───┬───────────┴─────────┴────────┘
                                │   │
                        PHONE: ±37°, metric to ~15 m
                                └─┬─┘
                              ██████████
                              overlap 30-37°
                              (guards Δ error)

   seams at ±90° ──┘        └── land on crop boundaries, so
                                adjacent crops both see them
```

| crop | covers | notes |
|---|---|---|
| C0 | −30° … +30° | front. Phone's territory — droppable once the pipeline works (~17% saving) |
| C1 | +30° … +90° | right front-quarter. **Crossing cyclists, cars turning in** |
| C2 | +90° … +150° | right rear-quarter |
| C3 | +150° … −150° | rear, wraps ±180°. **Overtaking cyclists.** Seam-free |
| C4 | −150° … −90° | left rear-quarter |
| C5 | −90° … −30° | left front-quarter. **Crossing cyclists, cars turning in** |

---

## iPhone (chest-mounted)

### Hardware it uses

| Component | Use |
|---|---|
| Wide camera via `AVCaptureSession` | The only video source. Landscape, tilted −15° |
| Neural Engine | YOLO + Depth Anything V2 Small, interleaved |
| Core Haptics | Directional haptic patterns — the primary output channel |
| Speakers / AirPods (A2DP out) | TTS and audio beacons |
| Microphone (**iPhone's, not AirPods'**) | Voice commands |
| CoreLocation | GPS + true heading — **navigation only**, never the awareness path |
| ~~LiDAR~~ | **Not used.** Dropped — see sync doc §2.1 |
| ~~ARKit~~ | **Not used.** No LiDAR means no reason to pay ARKit's cost |
| ~~AirPods head tracking~~ | **Not used.** Only the hat mount needed it |

### What it computes on-device

```
AVCaptureSession frame
  ├──► YOLO (CoreML) ─────────────► boxes + classes        every frame
  ├──► Depth Anything V2 Small ──► relative depth map      5-10 Hz, interleaved
  │         └──► ground-plane scale anchor ──► metres
  ├──► median depth inside each box ──► range_m per object
  └──► box centre column ──► bearing_deg (torso-relative)
                                  │
                                  ▼
                           AwarenessEvent(source="phone")
```

### What the phone OWNS

- **The front sector, ±37°, to ~15 m.** The collision zone. On-device, <100 ms, never leaves the device.
- **Metric range** — the only component allowed to state metres, and only while scale anchoring is healthy.
- **The master clock.** Everything else syncs to it.
- **All output.** Haptics, TTS, spatial audio, priority queue, urgency policy.
- **Voice intent recognition.**
- **Sign and text reading** — `VNRecognizeTextRequest` needs a rectilinear view; fisheye crops would wreck OCR.
- **Navigation** — routes, heading, turn cues.

### What the phone explicitly does NOT do

- Does not see past ±37°. Anything outside that is the X4's job, full stop.
- Does not detect drop-offs. **The white cane does that tactilely** — see sync doc §2.3.
- Does not stream video anywhere. Only events leave the phone.
- Does not compute urgency from someone else's opinion — it computes urgency for *all* events, including the X4's.

---

## Insta360 X4 → laptop → cloud

### Hardware

| Component | Use |
|---|---|
| Insta360 X4, shoulder mount | Lens axes front/back, so seams land at ±90° |
| USB-C → laptop in a backpack | Webcam mode, 2880×1440 equirect @ 30 fps |
| Laptop (M2 Pro) | Grab, dewarp, encode, send. Also the local-CoreML fallback detector |
| Cloud NVIDIA L4, `us-east1` | TensorRT YOLO + ByteTrack (primary) |

### The pipeline

```
X4 equirect 2880×1440 @30fps
  │
  ├─ latest-frame grab thread ─────────► one slot, never a queue
  │
  ├─ 6 × 60° perspective crops ───────► precomputed remap LUT
  │
  ├─ JPEG q60-70 ────────────────────► one request in flight, EVER
  │
  ▼
cloud L4: TensorRT YOLO ──► ByteTrack ──► track_id, bearing rate, box growth
  │                                               │
  │                                        closing_mps, range_band
  ▼
laptop ──► AwarenessEvent(source="x4_cloud") ──► phone
```

### What the X4 path OWNS

- **Everything outside ±30°** — sides, front-quarters, rear. The phone physically cannot see it.
- **The front sector beyond ~15 m** — existence only, no range.
- **Tracking and closing speed.** ByteTrack gives stable `track_id`s, which is what makes refractory suppression possible. The phone has no tracker.
- **`range_band`** — `near` / `mid` / `far` from box height.
- **Hazard map writes** to Firestore.

### What the X4 path explicitly does NOT do

- **Never states metres.** Monocular through a fisheye with no usable ground plane. A number would be a guess dressed as a measurement.
- **Never computes urgency.** It emits facts; the phone decides what matters.
- **Never reads its own clock in the cloud.** `t_capture` is stamped on the laptop and echoed through untouched.
- Does not do OCR or face work — fisheye crops are wrong for both.
- Does not touch the safety-critical path. If it dies, front warnings keep working.

---

## Feature ownership

| Feature | Device | Owner | MVP? |
|---|---|---|---|
| Voice control ("take me to X", "what's around me") | phone | P3 | ✅ |
| Front hazard detection + range | phone | P1 | ✅ |
| Side / rear / front-quarter awareness | X4 → cloud | P2 | ✅ |
| Turn-by-turn with directional haptics | phone | P3 | ✅ |
| Feedback manager (TTL, dedup, refractory, urgency, priority) | phone | P3 | ✅ |
| Clock handshake + event transport | both | P1 + P2 | ✅ |
| Sign / text reading | phone | P3 | ✅ cheap |
| Scene description — "in front of me" | phone frame → VLM | P3 | ✅ |
| Scene description — "around me" | 2–4 X4 crops → VLM | P2 + P3 | ✅ |
| Hazard map write | cloud → Firestore | P2 | ✅ |
| Local CoreML fallback detector | laptop | P2 | ✅ |
| Latency instrumentation + benchmark slide | both | P2 | ✅ |
| Last-50-feet (Places + Street View) | phone + cloud | P3 | 🔶 swing |
| ADK Loop/Parallel orchestration | cloud | P2 | 🔶 swing |
| A2A hazard-map agent | cloud | P2 | 🔶 swing |
| Drop-off detection | — | — | ❌ cut |
| Consent-based face memory | — | — | ❌ cut |
| Spatial audio via `AVAudioEnvironmentNode` | — | — | ❌ cut → stereo panning |
| Apple Watch haptics, transit mode, crosswalk assist, "guide me to my ride", elevation scoring, fall detection | — | — | ❌ cut |

**Note the VLM split.** "Describe what's in front of me" uses the phone's rectilinear frame.
"Describe what's around me" uses X4 crops — **never the raw equirect**, because VLMs are not
trained on equirectangular projection and will describe it badly.

---

## Three-person lanes

The split follows the `AwarenessEvent` contract, which means the iOS app divides along a
**perception / experience seam** rather than by screen.

### P1 — Perception (iOS), critical path

Give this to the strongest Swift dev.

`AVCaptureSession` · YOLO CoreML · Depth Anything V2 Small · ground-plane scale anchoring ·
box→depth sampling · bearing computation · intrinsics-derived FOV · WebSocket client ·
NTP handshake. **Produces `AwarenessEvent(source="phone")` and nothing else.**

### P2 — X4 pipeline (bridge + cloud)

UVC latest-frame grab thread · 6-crop dewarp LUT · JPEG encode · one-in-flight sender ·
GPU VM + TensorRT engine build · ByteTrack · closing speed · `range_band` · health monitor +
rung stepping + local CoreML fallback · WebSocket server · latency instrumentation ·
Firestore writes. **Swing item: ADK + A2A.**

### P3 — Experience (iOS) + navigation + pitch

Feedback manager (validate → stale → dedup → refractory → urgency → priority queue) · Core
Haptics patterns · TTS · voice intents · Routes API + turn cues · sign reading · VLM scene
description · pitch deck · demo script · demo route recon.

**P3 starts against the golden fixture file on hour 1** — no camera, no laptop, no cloud needed.
By the time P1 and P2 have live events, the consumer is already tested.

### Why the seam is here

The original plan's P1 lane held both perception *and* the feedback manager, which with one fewer
teammate would have made it the bottleneck everything else waits on. Splitting at the contract
means the hardest-to-debug work (P1) and the most demo-visible work (P3) proceed independently.

---

## Decisions this doc still needs from the team

1. **Who is P1?** It is the critical path, and it is the lane that most needs the strongest Swift dev.
2. **ADK go/no-go at hour 20.** It is a whole track, but it sits at 1–2 Hz outside the safety path and the demo works without it. Decide the deadline now, not at hour 30.
3. **Is cutting face memory agreed?** It is the single biggest scope saving here — a separate embedding model, encrypted storage, an enrollment flow, and a privacy story, for the least differentiated feature on the list.
4. **Stereo panning instead of `AVAudioEnvironmentNode`?** Real spatial audio is hours of work; L/R panning plus haptics conveys direction nearly as well for a demo.
5. **Does the RTX laptop have a role?** A local TensorRT bridge would cut the uplink hop entirely, but it loses the cloud story. Only relevant if someone owns that hardware.
