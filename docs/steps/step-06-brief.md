# Task 6 brief — X4 latest-frame grab + 6-crop dewarp + sender (bridge)

Extracted from docs/orchestration-runbook.md (Step 6), docs/camera-sync-design.md §1.3, §3.2, §3.3, §3.5, docs/device-responsibilities.md (X4 pipeline + crop table), docs/insta360feed.md (macOS capture facts), plus controller rulings. Exact values are binding.

## A. Runbook step text (verbatim)

## Step 6 — X4 latest-frame grab + 6-crop dewarp + sender

**Intent:** The bridge. Both of its rules fail silently, which is what makes this step dangerous.
**Chain rationale:** `performance-optimizer` because frame age at send is the entire deliverable —
correctness here is a latency property, not a functional one.

```bash
/everything-claude-code:orchestrate custom "ecc:tdd-guide,ecc:performance-optimizer,ecc:python-reviewer" "[Plan: docs/camera-sync-design.md#step-6] P2 lane bridge: grab the X4 stitched 2880×1440 equirect over UVC on a dedicated thread that keeps only the LATEST frame in one slot, never a queue, because CAP_PROP_BUFFERSIZE=1 is frequently ignored on macOS AVFoundation. Dewarp six 60° perspective crops via a precomputed OpenCV remap LUT, encode JPEG q60–70, and send with ONE REQUEST IN FLIGHT EVER — if a request is still pending when the next frame is due, drop that frame. Acceptance: measured frame age at send under 50 ms while the uplink is throttled; latency stays bounded under induced network delay"
```

---


## B. Spec: camera-sync-design.md §1.3, §3.2, §3.3, §3.5 (verbatim)

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

### 3.5 Cloud gotchas

1. **A TensorRT engine is not portable across GPU architectures.** Build the engine *on* the L4 you
   deploy to, not on a dev box, or it will not load. Bake this into the container build.
2. **Cold start is 30–60 s+.** Never let the demo be the first request — run a warm-up ping loop
   from the moment you walk to the stage.
3. **Region: `us-east1`** (South Carolina) is the closest GCP region to Florida, ~20–30 ms RTT.
4. **The hotspot carries everything** — cloud uplink, events, Tier 3 Gemini calls. Measure at the
   venue and pick a rung.

---


## C. Spec: device-responsibilities.md crop table and X4 pipeline (verbatim)

| crop | covers | notes |
|---|---|---|
| C0 | −30° … +30° | front. Phone's territory — droppable once the pipeline works (~17% saving) |
| C1 | +30° … +90° | right front-quarter. **Crossing cyclists, cars turning in** |
| C2 | +90° … +150° | right rear-quarter |
| C3 | +150° … −150° | rear, wraps ±180°. **Overtaking cyclists.** Seam-free |
| C4 | −150° … −90° | left rear-quarter |
| C5 | −90° … −30° | left front-quarter. **Crossing cyclists, cars turning in** |

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
- Does not do face work — fisheye crops are wrong for it.
- Signage: amended 2026-09-27. The crops this step produces are rectilinear, so reading signs off
  them is sound; the sweep that does it lives in `scene/signscan.py` and is off the safety path.
- Does not touch the safety-critical path. If it dies, front warnings keep working.

---


## D. Spec: insta360feed.md — macOS capture facts (verbatim)

## Insta360 X4 hardware

In USB webcam mode the X4 is a standard UVC camera named "Insta360 X4" with exactly two modes, and the 2:1 one is the whole sphere.

| Mode | Frame | What it shows | How to get it |
| --- | --- | --- | --- |
| Flat | 1920x1080, 30 fps | One dewarped forward view | Default; `--webcam N` or `--webcam-name insta360` |
| Panorama | 2880x1440, 30 fps | Stitched equirectangular sphere | `--webcam-name insta360 --cam-size 2880x1440 --equirect` |

**Enabling webcam mode.** Plug in USB-C, then choose Webcam as the USB mode on the camera screen (Settings > General > USB Mode if it does not prompt). The camera drops off USB when its screen sleeps; set the screen and auto-off timers to never before a walk and use a short, sturdy cable.

**macOS specifics, all hit and solved today**

- OpenCV cannot select the 2880x1440 mode: its AVFoundation backend hardcodes a session preset that resets the device format, whatever size you request. The harness therefore has a native AVFoundation source (`harness/sources/avfoundation_source.py`) for `--webcam-name`, which selects the camera by name, sets the active format, and holds the device configuration lock for the whole session; releasing the lock lets the preset win again. `AVCaptureSessionPresetInputPriority` is iOS-only and raises on macOS.
- OpenCV camera indices move: it sorts cameras by unique ID, so the X4's `--webcam N` changes whenever the iPhone Continuity Camera appears. Use `--webcam-name insta360`, or `python -m scripts.list_cameras` right before a run.
- "Cannot Use Insta360 X4" when setting the format: macOS's video-conference daemon holds the configuration lock even with no app using the camera. Restart it and rerun: `pkill -x avconferenced`.
- The terminal app needs camera permission (System Settings > Privacy & Security > Camera); OpenCV logs "not authorized to capture video" otherwise.

**Live streaming without USB.** Insta360 documents two flows. With camera firmware 1.2.21 or newer and app 1.68.0 or newer, the camera joins an external Wi-Fi itself (phone hotspot or router), the app hands over the credentials over Bluetooth, and the camera pushes RTMP directly in "360 Live" mode ([FAQ](https://onlinemanual.insta360.com/x4/en-us/faq/feature/livestream), [tutorial](https://onlinemanual.insta360.com/x4/en-us/camera/appuse/live)). Older versions use the phone as a relay: phone on the camera's Wi-Fi, pushing over mobile data, which needs an internet-reachable server. Our unit currently shows the relay flow. Expect 0.5 to 1.5 s latency for the direct flow and 1 to 3 s for the relay; see Wireless streaming below.


## E. What earlier steps built (use, do not re-implement)
- Step 2: `backend/` uv project; `cane.contracts.awareness_event` → `AwarenessEvent`, `Source`, `ObjectClass`, `RangeBand`, `parse_awareness_event`, `AwarenessEventError`. Bearing must be normalized into `(-180, 180]` before constructing an event (add 360 if `<= -180`).
- Step 3: `cane.transport.server.EventLinkServer` with `phone_clock.capture_time_ms(t_arrival_laptop_ms)` and `await send_event(event) -> bool`; `cane.transport.clock_sync.PhoneClock`. Check the real names before coding.
- The prototype at `~/Desktop/insta360/harness/` (outside this repo, the user's own code — copy freely with a one-line attribution comment): `sources/avfoundation_source.py` (selects the X4 by name and forces the 2880×1440 format via PyObjC AVFoundation — OpenCV cannot select that mode on macOS), `sources/latest.py` (`LatestFrameStore`: condition-variable single-slot newest-frame store), `sources/base.py` (`Frame`, `FrameSource` protocol), `sources/opencv_source.py` (file / index / RTSP sources), `projection.py` (cached remap idea). Read them first.

## F. Controller rulings — binding

### F1. Package layout (`backend/src/cane/bridge/`)
```
bridge/
  __init__.py
  config.py          # frozen BridgeConfig: camera selector, rung, delta_deg (Δ yaw calibration, default 0.0), pipeline_delay_d_ms, inference_url, request_timeout_s, send_fps
  rungs.py           # the four rungs from §3.2 as frozen dataclasses: (crop_px, jpeg_q, fps) → RUNG_1..RUNG_4, RUNGS tuple, bitrate_estimate_mbps
  capture/base.py    # Frame(image, t_arrival_laptop_ms:int, seq:int), FrameSource protocol
  capture/latest.py  # LatestFrameStore (ported from the prototype; ms int timestamps)
  capture/grab_thread.py   # dedicated thread: source.read() forever → store.push(); never a queue
  capture/avfoundation_source.py  # ported from prototype, only if PyObjC is available (optional extra `mac`); imports guarded
  capture/opencv_source.py        # file / index source for tests and non-mac
  capture/synthetic_source.py     # generates equirect frames at a given fps with a burned-in ms counter (for tests + spike 4)
  dewarp.py          # precomputed remap LUTs: equirect → six 60° perspective crops; CropSet(images, yaws_deg, t_arrival_laptop_ms)
  sender.py          # OneInFlightSender: at most one HTTP request pending, ever; drops the frame if busy; measures frame age at send
  events.py          # cloud response → AwarenessEvent(source=x4_cloud): bearing = crop_yaw + (cx-0.5)*60 + delta_deg, normalized; t_capture from the echoed stamp
  pipeline.py        # wires grab thread → dewarp → sender → events → EventLinkServer.send_event; async loop paced by rung fps
  __main__.py        # `python -m cane.bridge --source synthetic|file:PATH|x4 --rung 1 --inference-url http://... --ws-port 8765`
```
Add to `pyproject.toml` deps: `numpy`, `opencv-python-headless`, `httpx`; optional extra `mac = ["pyobjc-framework-AVFoundation", "pyobjc-framework-CoreMedia"]`. Do not import PyObjC at module top level anywhere that tests import.

### F2. The two silent rules (both must be tested, not just written)
1. **Newest frame only.** `grab_thread` reads the source continuously and pushes into the single-slot `LatestFrameStore`. The pipeline consumer calls `wait_for_new(last_seq, timeout)` and always receives the newest frame; intermediate frames are discarded. Never a `queue.Queue`.
2. **One request in flight, ever.** `OneInFlightSender.try_send(cropset) -> SendOutcome` returns `SendOutcome.dropped_busy` immediately if a request is pending; otherwise it starts the request. Record `frame_age_at_send_ms = now_ms − cropset.t_arrival_laptop_ms` for every request actually sent, and expose a rolling p50/p95 (`SenderStats`, frozen, computed from a bounded deque of the last 100).

### F3. Dewarp geometry (own implementation, no py360convert dependency)
- Six crops, yaw centres `0, 60, 120, 180, -120, -60` degrees (C0..C5 in the device-responsibilities table), horizontal FOV 60°, square output `crop_px × crop_px` (640 or 512 per rung), pinhole model: focal `f = (crop_px/2) / tan(30°)`. For each output pixel compute the ray, rotate by yaw, convert to (lon, lat), then to equirect `(u, v)`: `u = (lon/2π + 0.5) * W`, `v = (0.5 − lat/π) * H`. Build `map_x`, `map_y` float32 arrays once per (rung, frame size) with numpy — a `functools.lru_cache`d builder keyed on `(W, H, crop_px)` — and dewarp with `cv2.remap(..., cv2.INTER_LINEAR, borderMode=cv2.BORDER_WRAP)`. Equirect column 0 is bearing −180 and the centre column is bearing 0 (forward lens axis); if the X4's forward is not at the centre, `delta_deg` absorbs it.
- Bearing of a detection: `bearing_deg = yaw_deg + (cx_norm − 0.5) * 60 + delta_deg`, then normalize into `(-180, 180]`.
- Test the LUT with synthetic frames: paint a vertical stripe at a known equirect column and assert it lands in the expected crop at the expected normalized x (±1 px); assert the C3 rear crop wraps across the ±180 seam without a visible discontinuity (the stripe at column 0 appears at the centre of C3).

### F4. Inference API contract (shared with step 7 — define it here, step 7 implements the server)
File `backend/src/cane/cloud/api_models.py` (Pydantic, frozen, strict):
- `InferenceRequest` is sent as `multipart/form-data`: field `meta` (JSON of `InferenceMeta`) + six files `crop_0`..`crop_5` (JPEG bytes). `InferenceMeta { schema: 1, t_capture_ms: int (already PHONE-clock, computed on the laptop via phone_clock BEFORE sending), rung: int, crop_px: int, yaws_deg: [6 floats], session_id: str }`.
- `InferenceResponse { schema: 1, t_capture_ms: int (ECHOED, byte-identical), inference_ms: float, detections: [Detection] }` with `Detection { crop_index: int 0..5, cx: float 0..1, cy: float 0..1, w: float 0..1, h: float 0..1, cls: ObjectClass, confidence: float, track_id: str, closing_mps: float|null, range_band: RangeBand|null }`. The cloud never emits bearings or metres; the bridge computes bearing. Producers map COCO names to the closed enum (table in `contracts/README.md`).
- The bridge builds `AwarenessEvent(schema=1, source=x4_cloud, ...)` using the cloud's `track_id` **as given** (do not prefix it with the crop index — it must stay stable frame to frame for one physical object; the cloud tracks per crop for now, so a seam-crossing object gets a new id, which is accepted and handled by the phone's dedup). `t_capture_ms` = the echoed value; `ttl_ms` from a bridge-side TTL table identical to §4.4 (`cane/bridge/ttl.py`: person 1500, cyclist 500, scooter 500, car 400, bus 400, static classes 8000, unknown_obstacle 1500); `range_m = None` always; `range_band` and `closing_mps` from the detection.

### F5. Pipeline loop and latency accounting
- Loop paced at the rung's fps with `asyncio`; each tick: take newest frame (skip if none newer), stamp `t_capture_phone_ms = phone_clock.capture_time_ms(frame.t_arrival_laptop_ms)` (if the clock is not synced yet, skip the frame and log once per 5 s), dewarp, encode JPEG at rung quality, `try_send`; on response, build events and `await server.send_event(...)` for each.
- The grab thread and the dewarp run in threads (`asyncio.to_thread`) so the event loop never blocks on `cv2`.
- Log a one-line stats summary every 5 s: grab fps, send fps, dropped_busy count, frame age p50/p95 at send, request RTT p50/p95.

### F6. Tests (`backend/tests/bridge/`, TDD, no hardware)
- `test_latest_store.py`: newest wins (push 1..10 quickly, one wait returns 10); wait after fail returns None; sequence numbers monotonic.
- `test_grab_thread.py`: with a `SyntheticSource` at 30 fps and a slow consumer (sleep 200 ms), the consumer always receives a frame whose age is < 40 ms at pickup.
- `test_dewarp.py`: F3 stripe tests; LUT cache hit (builder called once for two frames); output shapes for both crop sizes.
- `test_sender.py`: a fake transport whose request takes 300 ms; feed frames every 100 ms for 2 s → exactly the expected number of sends (~6–7), the rest `dropped_busy`, and frame age at send p95 < 50 ms **even though the transport is slow** (the acceptance criterion); with induced transport delay growing to 2 s, the in-flight count never exceeds 1 and frame age at send stays bounded (< 50 ms).
- `test_events.py`: bearing math for each crop including wrap (C3 cx=0.5 → 180.0; C3 cx=0.6 → −174.0); `delta_deg` applied; `range_m` is None; ttl from table; the produced event passes `parse_awareness_event`.
- `test_pipeline.py`: end-to-end with `SyntheticSource`, a fake inference transport returning one detection, and a real `EventLinkServer` on localhost with a fake phone client → the phone receives a valid `x4_cloud` event whose `t_capture_ms` equals the echoed stamp.
- A manual spike-4 helper: `python -m cane.bridge.spike4 --source x4` displays a ms counter window and reports `D = t_arrival − t_displayed` median/spread by OCR-free method (user reads the digits) — keep it tiny; it is documentation of the method from §4.3, not a tested component.

### F7. Out of scope
The inference server itself (step 7); rung stepping and the local CoreML fallback (step 8); Firestore (15).

## G. Global constraints (verbatim)
- "Grab: newest frame only … Run a dedicated thread that reads continuously and keeps only the latest frame in a single slot."
- "Send: one request in flight, ever. If a request is still pending when the next frame is due, drop that frame."
- "Acceptance: measured frame age at send under 50 ms while the uplink is throttled; latency stays bounded under induced network delay."
- "The cloud is timeless: echo t_capture and never read the local clock."
- Commit format `<type>: <description>`; no attribution trailers. Immutable config; small files; explicit error handling; constants not magic numbers.
