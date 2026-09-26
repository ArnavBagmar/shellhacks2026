# Camera Sync Design — Insta360 X4 + iPhone

**Status:** Complete and agreed (§0–§4) · ready for implementation planning
**Last updated:** 2026-09-26
**Companion doc:** `device-responsibilities.md` — who owns which sector and which feature
**Supersedes parts of** `smart-cane-shellhacks-plan.md (1).pdf` — see [§5 Amendments](#5-amendments-to-the-master-plan).

> Living doc. Edit freely. If you change a decision, add a line to [§7 Decision log](#7-decision-log)
> so nobody re-litigates it at 3am.

---

## The headline

**With both cameras rigidly mounted on the torso, we do not need synchronized shutters.**

We need exactly two things:

1. **One shared clock** — so stale warnings get dropped instead of announced late.
2. **One constant rotation** — so image bearings become body bearings.

Frame-level sync was only ever required by the original plan because the phone was on a
*sweeping cane*: a live, noisy, unknown rotation between the two sensors. Chest-mounting the
phone deletes that unknown, and the "cane sweep IMU compensation" risk item goes with it.

---

## 0. Hardware topology

```
        ┌─────────────── RIGID BODY (torso) ───────────────┐
        │                                                   │
        │   Insta360 X4 (shoulder)      iPhone (chest)      │
        │   lens axes front/back        landscape, -15°     │
        │        │                           │              │
        │        │ bearing θ_360             │ bearing θ_ph │
        │        └────────── Δ ──────────────┘              │
        │              ONE CONSTANT                          │
        └───────────────────────────────────────────────────┘
                 │                           │
         USB-C   │                           │ on-device
                 ▼                           ▼
        laptop: grab → dewarp          AVCaptureSession
                 │  6 × 60° crops      + YOLO + depth model
                 │ JPEG                       │  < 100 ms
                 ▼                            │
        cloud L4: TensorRT YOLO               │
                 │  + ByteTrack               │
                 ▼  events                    │
              laptop ──── hotspot LAN ──► feedback manager
                                             (haptics / spatial audio / TTS)
```

The phone owns **"in front of me, now"** (on-device, <100 ms). The X4 owns **"everything the
phone can't see"** (cloud, budgeted 150–400 ms). They meet as small JSON events.

---

## 1. Reference frames

### 1.1 Bearings are torso-relative

**`0°` = the direction the chest faces · `+` = right · range `(-180°, 180°]`**

Not travel-relative. Travel direction comes from `CLLocation.course`, which is unreliable below
~0.5 m/s — exactly when a user is standing at a crosswalk deciding whether to step off the curb.
That is the worst possible moment for the reference frame to become undefined.

Torso-relative has no such hole, needs **no GPS, magnetometer, or course estimate anywhere in the
awareness path**, and matches what a user intuits: "on your left" means left *of your body*.

Navigation still needs world heading for "turn left in 20 m" — that is CoreLocation on the phone,
and it never touches the event path.

### 1.2 The one constant, Δ

Both cameras' forward axes are fixed relative to the torso, so the yaw offset between them
**never changes while mounted**. It is a number in a config file, not a live estimate.

**Calibration (two minutes, once per mounting session):**

1. Stand facing a doorway or any tall vertical edge, square on.
2. Note the image column where that edge appears in the X4 equirect.
3. Note the image column where it appears in the iPhone frame.
4. Convert both to angles; `Δ = θ_360 − θ_phone`. Store in config.

### 1.3 X4 coverage: full equirect, six 60° crops

**Use the stitched 2880×1440 equirect that webcam mode provides, and dewarp six 60° perspective
crops covering the full 360°.**

An earlier draft of this doc processed only the rear fisheye. That left a real hole: a 200° rear
fisheye covers bearings 80°–180°, the phone covers ±37°, so **37°–80° was seen by neither camera**
— the front-quarters, where crossing cyclists and vehicles turning into you appear.

Six uniform crops also means one LUT array and no special cases, which is easier to debug than an
asymmetric layout. Once it works you can stop sending the front crop (the phone owns it) for a
~17% saving — but ship uniform first.

**Mount orientation: lens axes front/back** (unchanged). This puts the stitch seams at ±90°.
That is the right trade: a crossing hazard sweeps through ±90° in under a second and gets detected
at 60° and 120° either side, with tracking bridging the gap — whereas an **overtaking cyclist sits
near 180° for seconds**, so keeping the rear seam-free protects a persistent, high-value detection.

On-camera stitching is good enough for objects beyond ~1.5 m. Our hazards live at 5–20 m. Do not
spend hackathon hours optimising around the seam.

### 1.4 Phone mount geometry

**Landscape, tilted ~15–20° down, chest height (~1.3 m).**

Landscape puts the wide camera's ~73° horizontal FOV (≈ ±37°) where obstacle coverage is needed.

**Do not hardcode ±37°.** Derive it at startup from the camera intrinsics:

```swift
// AVCaptureConnection.isCameraIntrinsicMatrixDeliveryEnabled = true
// then read kCMSampleBufferAttachmentKey_CameraIntrinsicMatrix per sample buffer
let halfFOV = atan(cx / fx)
```

Note we are **not using ARKit** — see §2.1. Intrinsics come from the sample buffer attachment,
not `ARCamera`.

---

## 2. Perception

### 2.1 Phone: YOLO + monocular depth, no LiDAR, no ARKit

LiDAR is out. Range comes from a CoreML monocular depth model, sampled inside YOLO boxes.

**This is a net win beyond just simplicity:** it runs on *any* iPhone rather than Pro models only,
which directly strengthens the Google.org pitch line about hardware people already own. It is also
not capped at LiDAR's ~5 m.

**Dropping LiDAR also drops ARKit.** Plain `AVCaptureSession` is lighter, lower latency, and
avoids world-tracking CPU cost. Nothing in the awareness path needs ARKit any more.

**Model:** [`apple/coreml-depth-anything-v2-small`](https://huggingface.co/apple/coreml-depth-anything-v2-small)
— 24.8M params, DPT/DINOv2, Apple-converted. **31.1 ms on an iPhone 12 Pro Max**, so expect
meaningfully faster on current hardware. F16, F16INT8 and F32 variants available; sample Swift
lives in `huggingface/coreml-examples`.

```
frame
  ├──► YOLO (CoreML) ──────────► boxes + classes
  │                                    │
  └──► Depth Anything V2 ──► dense relative depth
                                       │
                          median depth inside each box
                                       │
                                  range per object
```

**Budget (estimate — measure it, spike 9):**

| step | est. |
|---|---|
| AVCapture → pixel buffer | 30–50 ms |
| YOLO (n/s @ 640) | ~15 ms |
| Depth Anything V2 Small | ~15–20 ms |
| box sampling + plane fit | ~2 ms |
| **total** | **~70–90 ms** — inside the <100 ms Tier 1 budget |

**Rate-limit the depth model.** Existence changes faster than range. Run YOLO every frame and
depth at 5–10 Hz, interleaved so the two never contend for the ANE on the same frame. This is the
main lever if the budget gets tight.

### 2.2 Scale anchoring — turning relative depth into metres

Depth Anything V2 Small outputs **relative** depth. Metric variants exist but are too heavy for
real-time on-device. So we anchor the scale.

**Primary anchor: the ground plane.** The camera sits at a known height on a chest mount (~1.3 m,
measured once per wearer). Fit the ground plane in the lower region of the relative depth map; the
scale factor is whatever makes the camera height come out to the measured value. This works with
nothing in view and updates continuously.

**Cross-check: known-height objects.** When YOLO finds a person, the box bottom plus camera
geometry gives an independent metric range (`range = cam_height / tan(angle_to_box_bottom)`).
Compare against the anchored depth.

**Log the scale factor.** If it drifts wildly or the two methods diverge, depth is untrustworthy
and the phone must **stop stating metres** and fall back to bands (§4.1). Silent bad depth is far
worse than admitted uncertainty.

### 2.3 What we gave up: drop-off detection

Drop-offs (descending curbs, stairs down) are a *ground-plane discontinuity*, not an object. YOLO
cannot box them, and monocular depth is weakest exactly there — textureless ground, subtle
geometry, and a known tendency to flatten ground planes.

**MVP position: the white cane detects drop-offs tactilely, and it is very good at it.** The
system extends the cane's reach to what it physically cannot touch — overhangs at head height,
approaching vehicles, cyclists behind you, torso-height obstacles, the door you are looking for.
"We don't replace the cane, we extend it" is a sharper story than a half-working curb detector.

Stretch, if §2.2's plane fit turns out solid: flag points deviating from the fitted plane. Test it
on a real curb before promising it in the demo.

---

## 3. Compute placement for the X4

**Primary: cloud NVIDIA L4 running TensorRT. Fallback: local CoreML on the M2 Pro.** Same
`Detector` interface, chosen at runtime by a health monitor.

### 3.1 Latency budget

| hop | local (M2 Pro) | cloud (L4, `us-east1`) |
|---|---|---|
| camera → laptop (UVC) | ~50 ms | ~50 ms |
| dewarp + encode (6 crops) | ~8 ms | ~12 ms |
| uplink to GPU | — | **~40–120 ms** |
| YOLO + ByteTrack | ~25–35 ms (CoreML) | ~12–25 ms (TensorRT) |
| events → phone | ~10 ms (hotspot LAN) | ~25–60 ms |
| feedback → haptic | ~30 ms | ~30 ms |
| **typical total** | **~125 ms** | **~175–300 ms** |
| **p95** | ~210 ms | ~400–600 ms |

TensorRT *wins* the inference row. The entire cost of the cloud is network round trip, and it
lands inside the 150–400 ms Tier 2 budget the plan already committed to.

A cyclist at 15 mph (6.7 m/s) covers ~1 m during the extra typical latency, ~3.4 m in the p95 tail.
Detected at 15–20 m, the warning still lands ~2 s before they pass. **This is acceptable only
because imminent hazards never leave the device.**

### 3.2 Uplink budget

Dewarp on the laptop (precomputed OpenCV remap LUT per crop, sub-millisecond), ship crops as
**JPEG, not H.264** — stateless, no GOP latency, and a failing frame can be dumped to disk.

| rung | crops | size | q | fps | uplink |
|---|---|---|---|---|---|
| 1 | 6 | 640×640 | 70 | 3 | ≈6.5 Mbps |
| 2 | 6 | 640×640 | 70 | 2 | ≈4.3 Mbps |
| 3 | 6 | 512×512 | 60 | 3 | ≈2.3 Mbps |
| 4 | 6 | 512×512 | 60 | 2 | ≈1.5 Mbps |

The health monitor steps down these rungs. Resolution *is* the detection range knob:

```
640 crop / 60° FOV = 10.7 px/deg → cyclist (1.8 m) at 20 m = 55 px  ← comfortable
512 crop / 60° FOV =  8.5 px/deg → cyclist (1.8 m) at 20 m = 44 px  ← fine
```

60° crops are narrower than the 90° assumed in an earlier draft, so pixel density is better than
previously estimated. Numbers above are un-measured — see [§6](#6-spike-list).

### 3.3 The two rules that make or break this

Both fail **silently**: nothing errors, warnings just quietly arrive late.

1. **Grab: newest frame only.** We capture at 30 fps and process at 2–3. The driver buffers, and
   `CAP_PROP_BUFFERSIZE=1` is frequently ignored on macOS AVFoundation. Run a dedicated thread
   that reads continuously and keeps only the latest frame in a single slot. Skip this and you are
   serving frames 100–300 ms behind reality.
2. **Send: one request in flight, ever.** If the uplink is slower than the send rate, requests
   queue and latency grows without bound. If a request is still pending when the next frame is
   due, **drop that frame**. Bounds latency structurally instead of hoping the network cooperates.

### 3.4 Automatic fallback

Not a keystroke — a hall with a thousand people on 5G is a genuinely saturated uplink, and a manual
switch will not get flipped in time on stage.

```python
# Step DOWN a rung when:  p95 round-trip > 500 ms over last 10 frames
# Flip to LOCAL when:     3 consecutive timeouts
#                     OR  already on rung 4 and still failing
#                     OR  stale-drop rate crosses threshold (§4.4)
# Recover:                step up one rung after 30 s healthy
# User-visible:           nothing. Silent, logged.
```

### 3.5 Cloud gotchas

1. **A TensorRT engine is not portable across GPU architectures.** Build the engine *on* the L4 you
   deploy to, not on a dev box, or it will not load. Bake this into the container build.
2. **Cold start is 30–60 s+.** Never let the demo be the first request — run a warm-up ping loop
   from the moment you walk to the stage.
3. **Region: `us-east1`** (South Carolina) is the closest GCP region to Florida, ~20–30 ms RTT.
4. **The hotspot carries everything** — cloud uplink, events, Tier 3 Gemini calls. Measure at the
   venue and pick a rung.

---

## 4. Clock, staleness, and the event contract

### 4.1 Three clock decisions

1. **The phone is the master clock.** It is what acts and what the user experiences.
2. **The cloud is timeless.** It echoes `t_capture` and never reads its own clock.
3. **Events route cloud → laptop → phone**, not cloud → phone. Costs ~10 ms; buys a single clock
   pair to sync, and both detectors share one event path.

### 4.2 Offset handshake

Standard NTP four-timestamp exchange over the existing WebSocket:

```
laptop --PING(t1)--> phone          t1, t4 = laptop clock
laptop <--PONG(t2,t3)-- phone       t2, t3 = phone clock

offset = ((t2 - t1) + (t3 - t4)) / 2
rtt    =  (t4 - t1) - (t3 - t2)
```

Run every 5 s, keep the last 9 samples, and **use the sample with the lowest RTT — not the median
of the offsets.** That is what real NTP does: the lowest-RTT sample has the least path asymmetry,
and averaging drags it toward the jittery ones.

Over a hotspot LAN (RTT 5–20 ms) this pins the offset to ±10 ms. Re-handshaking every 5 s also
means a system clock jump self-corrects — do not build anything for that.

### 4.3 Pipeline delay constant, D

The offset converts laptop time to phone time. It does **not** say when photons hit the sensor.
UVC gives no trustworthy capture timestamp, so `t_arrival_laptop` trails true capture by exposure
+ ISP + USB + decode. That is `D`, roughly constant for a fixed resolution and fps.

Do not guess it. 30 ms and 150 ms are both plausible and mean very different things for a cyclist.

**Primary measurement (~5 min, no second device):** render a large millisecond counter on the
*laptop's own screen*, point the X4 at it, and in the grab loop compare the digits visible in the
frame against the laptop clock when the frame arrived.

```
D = t_arrival_laptop − t_displayed_in_frame
```

The displayed time *is* the capture time in the laptop's own clock, so this is self-referential and
exact. Take 20 samples, use the median, record the spread.

*Caveat:* the panel adds 1–2 frames (16–33 ms) of its own, so this slightly **under**-estimates D.
Acceptable inside a 100 ms+ budget, but know it is there.

**End-to-end cross-check:** flash the iPhone torch at the X4 on a bench and compare the phone's
timestamp for the flash against the laptop's `t_arrival` for the frame containing it. Yields
`D + offset` directly in phone-clock terms.

> **If the two measurements disagree by more than ~30 ms, the NTP offset is wrong.**
> Find that out on Friday afternoon, not on stage.

### 4.4 Staleness policy

`t_capture` exists for one purpose: deciding whether a warning is still true. Objects have
different half-lives, so one global timeout is wrong.

| class | TTL | reasoning |
|---|---|---|
| cyclist / scooter | 500 ms | 6.7 m/s → 3.4 m of closure; past that the bearing is a lie |
| moving car | 400 ms | faster closure |
| pedestrian | 1.5 s | ~1.4 m/s |
| pole, bollard, wall, parked_car, door, overhang | 8 s | it is not moving — only you are |
| scene description | 15 s | latency-tolerant by nature |

**The feedback manager drops. It never announces late.** Silence is a valid output — design
principle #2, with teeth.

Then reuse the signal: **count the drops.** A rising stale-drop rate is the cleanest trigger for
the §3.4 fallback — one mechanism, two jobs, no separate health probe.

### 4.5 Sector ownership

The partition is **angular × range**, not purely angular. The phone's monocular range stays
trustworthy to roughly 15 m; past that, boxes get small and depth gets soft.

```
bearing:  0°        30° 37°         90°               150°    180°
          │          │   │           │                 │       │
 PHONE    ██████████████                                          ±37°, to ~15 m
 X4              █████████████████████████████████████████████    ±30° → ±180°
          └─ phone ──┘└ ov ┘└────────── X4 exclusive ─────────┘
              only    30-37°
```

| sector | range | owner | why |
|---|---|---|---|
| front, \|θ\| ≤ 37° | ≤ 15 m | **phone** | on-device, <100 ms, metric — the collision zone |
| front, \|θ\| ≤ 37° | > 15 m | **X4** | existence only, no range |
| \|θ\| > 30° | any | **X4** | the phone physically cannot see it |

The 30–37° overlap exists so Δ calibration error cannot open a gap.

### 4.6 The event contract

Eleven fields. All three lanes code against this.

```python
@dataclass(frozen=True)
class AwarenessEvent:
    schema: int                # 1 — bump on any breaking change
    source: str                # "phone" | "x4_cloud" | "x4_local"
    track_id: str              # stable per object; the anti-flooding key
    t_capture_ms: int          # PHONE CLOCK, D-corrected. The canonical time.
    ttl_ms: int                # from the §4.4 table
    cls: str                   # closed enum below
    confidence: float          # 0..1
    bearing_deg: float         # torso-relative, 0 = ahead, + = right
    range_m: float | None      # phone only, and only while scale anchoring is healthy
    range_band: str | None     # "near" | "mid" | "far" — X4 always; phone when unanchored
    closing_mps: float | None  # + = approaching
```

**Two deliberate omissions, both load-bearing.**

`urgency` is **not** on the wire, though the original plan's format had it. **Producers emit facts;
the consumer computes urgency.** Urgency depends on user state — mid-crossing, stationary, already
warned — which only the phone knows. Bake it into the backend and you cannot change announcement
policy without a redeploy at hour 30.

`range_m` is **never** populated by the X4. It is monocular through a fisheye, with no usable
ground plane in a rear-facing crop; any number would be a guess dressed as a measurement. It emits
`range_band` from box height instead. The **phone** may state metres, but only while §2.2 scale
anchoring is healthy — if the anchor diverges, the phone drops to bands too.

Announcements round: **"about two metres," never "2.3 metres."** Urgency thresholds use bands with
hysteresis so a noisy estimate cannot flip urgency back and forth.

**Closed class enum** (both sides must agree, so it lives in one file):

```
moving:  person · cyclist · scooter · car · bus
static:  pole · bollard · wall · parked_car · door · overhang
catch:   unknown_obstacle
```

`unknown_obstacle` matters more than it looks: detectors find shapes they cannot label, and those
still need to reach the user.

### 4.7 Arbitration — the feedback manager pipeline

Every event runs this gauntlet in order:

1. **Validate** — schema version, enum membership, bearing in `(-180, 180]`, `ttl > 0`. Reject and
   log; never crash on a malformed event.
2. **Staleness gate** — `now − t_capture > ttl` → drop, increment the §3.4 counter.
3. **Dedup gate** — another event within 10° and 300 ms from a different source? Prefer the phone
   when it has healthy metric range inside 15 m; otherwise keep the X4's. Bearing + time
   proximity, not data association.
4. **Refractory suppression** — this `track_id` announced recently and urgency has not escalated?
   Drop. **This is the anti-flooding mechanism**, and it is why `track_id` is in the contract.
5. **Urgency computation** — on-device policy, below.
6. **Priority queue** — one announcement at a time; higher urgency preempts one in progress.

| urgency | trigger | output | refractory |
|---|---|---|---|
| **3 immediate** | time-to-contact < 2 s | haptic burst + one word ("Stop") | 0 — always speaks |
| **2 act soon** | TTC 2–5 s, or static obstacle < 2 m | directional haptic + 2–3 words | 2 s |
| **1 aware** | worth knowing, no action needed | **directional haptic only, no speech** | 8 s |
| **0 ambient** | — | log + hazard map, never announced | — |

`TTC = range_m / closing_mps`. Without metric range, `range_band` substitutes — `near` + fast
closing → urgency 2–3.

Urgency 1 being haptic-only is where "don't flood the ears" stops being a slide and becomes code.

### 4.8 Keeping two languages in sync

P1 and P3 write Swift, P2 writes Python, and at hour 20 they will disagree about a field
name. The fixture file is also how P3 builds the entire feedback manager on hour 1 with no
camera, no laptop and no cloud — see `device-responsibilities.md`.

- `contracts/awareness_event.schema.json` — single source of truth, in the repo
- Pydantic model on the backend (FastAPI already validates this way), `Codable` struct on Swift
- **A golden fixture file: ~10 events, valid and invalid, that both sides' test suites run against**

That fixture is the one test that catches cross-language drift, and it takes twenty minutes.

### 4.9 Free benchmark data

Every event carries `t_capture`, so the phone logs `now − t_capture` at announcement time. That is
a **measured** end-to-end latency distribution, per class, on real walking data. It is the latency
slide, and it is how TensorRT-vs-CoreML gets proven rather than asserted.

---

## 5. Amendments to the master plan

The PDF is still correct about pitch framing, tracks, and timeline. These items are **superseded**:

| PDF says | Now |
|---|---|
| iPhone mounted on the cane | **iPhone chest-mounted**, landscape, −15° |
| Chest-strap mount = "backup if the cane mount is too shaky" | Chest strap is **primary**; cane is a demo prop and a tactile drop-off detector |
| Insta360 on a hat | **Insta360 on a shoulder mount**, lens axes front/back |
| Risk: "cane sweep makes phone data shaky → IMU compensation" | **Resolved by the chest mount.** No sweep compensation needed |
| Tier 1 = ARKit `sceneDepth` (LiDAR) + CoreML | **No LiDAR, no ARKit.** `AVCaptureSession` + YOLO + Depth Anything V2 Small, scale-anchored to the ground plane |
| MVP: drop-off detection | **Cut from MVP.** The white cane does this tactilely — see §2.3 |
| iOS framework list includes ARKit | ARKit removed; intrinsics come from the sample buffer attachment |
| 360 compute on the laptop | **Cloud L4 + TensorRT primary, local CoreML fallback**, automatic |
| Insta360 → laptop → backend → WebSocket → phone | **X4 → laptop → cloud → laptop → phone** (one clock pair) |
| Event format `{type, direction, urgency}` | 11-field contract, §4.6. **`urgency` is computed on-device, not on the wire** |
| Team of 4 | **Team of 3** — see `device-responsibilities.md` |
| Production path: Insta360 → phone via Insta360 SDK | Still the right long-term answer. **Not the hackathon path** — see spike 8 |

**BOM additions:** shoulder/chest mount for the X4, short USB-C cable chest → backpack.

**BOM removals:** AirPods head tracking is not needed — that was only required by the hat-mounted
variant, so the AirPods mic is fine for voice input. **[SUPERSEDED 2026-09-26 — the mic is the iPhone's; see build-plan.md §6 conflict #1 and step-10-design.md Amendment 5. AirPods remain the preferred *output*.]** (Trap avoided: `AVAudioSession`'s
`.allowBluetooth` option silently stops `CMHeadphoneMotionManager` updates.)

---

## 6. Spike list

Verify with hardware before the clock starts. Unowned items are up for grabs.

| # | Spike | Time | Owner | Result |
|---|---|---|---|---|
| 1 | Does the X4 enumerate as a capture device to OpenCV/AVFoundation on macOS, or is it genuinely OBS-only? | 15 min | | |
| 2 | Is webcam-mode output raw or stabilized? Rotate the camera 90° in place: content rotates with it → raw; scene stays put → yaw is world-locked | 10 min | | |
| 3 | ~~Single-lens output in webcam mode~~ — **moot**, §1.3 uses the stitched equirect | — | — | dropped |
| 4 | Measure `D` with the screen-timer method; confirm the latest-frame grab thread is required | 20 min | | |
| 5 | Measure hotspot uplink RTT and sustained throughput **at the venue**; pick a §3.2 rung | 10 min | | |
| 6 | Verify §3.2 JPEG sizes against real outdoor frames | 10 min | | |
| 7 | TensorRT engine builds and loads on the target L4 | 1 h | | |
| 8 | **Submit the Insta360 SDK application — approval takes ~3 business days, so do this today** | 10 min | | |
| 9 | Measure YOLO + Depth Anything V2 Small together on the phone against the <100 ms budget | 30 min | | |
| 10 | Does ground-plane scale anchoring hold while walking? Log the scale factor over a 200 m walk | 30 min | | |
| 11 | Stretch: does plane-fit deviation find a real curb edge? Test on an actual curb | 30 min | | |

---

## 7. Decision log

| Date | Decision | Why |
|---|---|---|
| 2026-09-26 | iPhone moves from cane to chest | Deletes the unknown live rotation between sensors; makes tight sync unnecessary |
| 2026-09-26 | X4 moves from hat to shoulder | Torso-rigid means image bearings are body bearings via one constant. Kills head-yaw estimation, drift correction, calibration gesture, and the AirPods audio-session conflict |
| 2026-09-26 | Mount orientation: lens axes front/back | Seams land at ±90°, where hazards are transient and tracking bridges them. Keeps the rear seam-free for persistent overtaking detections |
| 2026-09-26 | Use the full stitched equirect, six 60° crops | Processing only the rear fisheye left 37°–80° uncovered — the front-quarters, where crossing hazards live |
| 2026-09-26 | UVC webcam mode over RTMP | RTMP adds 1–3 s with jitter. At 6.7 m/s that is a *wrong* warning, not a late one. RTMP stays viable for latency-tolerant features only |
| 2026-09-26 | Cloud L4 + TensorRT primary, local CoreML fallback | Restores the NVIDIA centerpiece, fits the 150–400 ms Tier 2 budget, yields a real measured benchmark. Safe only because Tier 1 never leaves the device |
| 2026-09-26 | Cloud is timeless; events route back via the laptop | Only one clock pair to sync; both detectors share one event path |
| 2026-09-26 | Stale events are dropped, never announced late | Design principle #2. Also makes the design absorb cloud flakiness with no new machinery |
| 2026-09-26 | Bearings are torso-relative, not travel-relative | `CLLocation.course` is unreliable below ~0.5 m/s — precisely when standing at a curb. Removes GPS from the awareness path entirely |
| 2026-09-26 | `urgency` is computed on-device, not on the wire | It depends on user state only the phone knows; on the wire it cannot be changed without a backend redeploy |
| 2026-09-26 | X4 never states metres, only bands | Monocular through a fisheye with no usable ground plane. A number would be a guess dressed as a measurement |
| 2026-09-26 | LiDAR and ARKit dropped; YOLO + Depth Anything V2 Small instead | Runs on any iPhone (strengthens the Google.org pitch), not capped at 5 m, and `AVCaptureSession` is lighter than ARKit |
| 2026-09-26 | Drop-off detection cut from MVP | Ground-plane discontinuities are monocular depth's weakest case. The white cane already does this well — "we extend the cane, we don't replace it" |

---

## 8. Open questions

- NVIDIA track framing now that TensorRT sits in the cloud rather than on the bridge laptop —
  prize strategy, not technical.
- Does the RTX laptop still have a role? A local TensorRT bridge would cut the uplink hop entirely
  but loses the cloud story.
- Which YOLO weights, and are the phone and cloud detectors sharing a class list? (They must, to
  satisfy §4.6.)
- Feature scope for a 3-person team — tracked in `device-responsibilities.md`.
