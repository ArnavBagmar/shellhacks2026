# Task 7 brief — Cloud L4 TensorRT + ByteTrack + closing speed

Extracted from docs/orchestration-runbook.md (Step 7), docs/camera-sync-design.md §3.1, §3.5, docs/build-plan.md F2 (cloud half), §4 cloud rows, §5, the task-6 brief's inference API contract (F4), plus controller rulings. Exact values are binding.

## A. Runbook step text (verbatim)

## Step 7 — Cloud L4 TensorRT + ByteTrack + closing speed

**Intent:** The NVIDIA centrepiece, and the source of every `track_id` the feedback manager relies on.
**Chain rationale:** `architect` first because engine-build-on-target-GPU is a container and
deployment decision, not a coding one; `mle-reviewer` covers the tracker and inference path.

```bash
/everything-claude-code:orchestrate custom "ecc:architect,ecc:tdd-guide,ecc:mle-reviewer,ecc:python-reviewer" "[Plan: docs/camera-sync-design.md#step-7] Stand up the L4 inference service in us-east1: FastAPI, TensorRT YOLO, ByteTrack for stable track_ids, bearing rate and box growth to closing_mps, box height to range_band. Build the TensorRT engine ON the target L4 inside the container build — engines are not portable across GPU architectures. The cloud is timeless: echo t_capture and never read the local clock. Acceptance: the engine loads on a fresh L4 boot; inference under 25 ms; track_id stable across 30 frames; no metres ever emitted. Out of scope: urgency, OCR, and face work"
```

---


## B. Spec: camera-sync-design.md §3.1 and §3.5 (verbatim)

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

### 3.5 Cloud gotchas

1. **A TensorRT engine is not portable across GPU architectures.** Build the engine *on* the L4 you
   deploy to, not on a dev box, or it will not load. Bake this into the container build.
2. **Cold start is 30–60 s+.** Never let the demo be the first request — run a warm-up ping loop
   from the moment you walk to the stage.
3. **Region: `us-east1`** (South Carolina) is the closest GCP region to Florida, ~20–30 ms RTT.
4. **The hotspot carries everything** — cloud uplink, events, Tier 3 Gemini calls. Measure at the
   venue and pick a rung.

---


## C. Spec: build-plan.md F2, §4 cloud rows, §5 (verbatim)

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


## D. The bridge↔cloud API contract (verbatim from task-6-brief.md §F4 — step 6 and step 7 both implement it; whichever runs first creates `cane/cloud/api_models.py`)

### F4. Inference API contract (shared with step 7 — define it here, step 7 implements the server)
File `backend/src/cane/cloud/api_models.py` (Pydantic, frozen, strict):
- `InferenceRequest` is sent as `multipart/form-data`: field `meta` (JSON of `InferenceMeta`) + six files `crop_0`..`crop_5` (JPEG bytes). `InferenceMeta { schema: 1, t_capture_ms: int (already PHONE-clock, computed on the laptop via phone_clock BEFORE sending), rung: int, crop_px: int, yaws_deg: [6 floats], session_id: str }`.
- `InferenceResponse { schema: 1, t_capture_ms: int (ECHOED, byte-identical), inference_ms: float, detections: [Detection] }` with `Detection { crop_index: int 0..5, cx: float 0..1, cy: float 0..1, w: float 0..1, h: float 0..1, cls: ObjectClass, confidence: float, track_id: str, closing_mps: float|null, range_band: RangeBand|null }`. The cloud never emits bearings or metres; the bridge computes bearing. Producers map COCO names to the closed enum (table in `contracts/README.md`).
- The bridge builds `AwarenessEvent(schema=1, source=x4_cloud, ...)` using the cloud's `track_id` **as given** (do not prefix it with the crop index — it must stay stable frame to frame for one physical object; the cloud tracks per crop for now, so a seam-crossing object gets a new id, which is accepted and handled by the phone's dedup). `t_capture_ms` = the echoed value; `ttl_ms` from a bridge-side TTL table identical to §4.4 (`cane/bridge/ttl.py`: person 1500, cyclist 500, scooter 500, car 400, bus 400, static classes 8000, unknown_obstacle 1500); `range_m = None` always; `range_band` and `closing_mps` from the detection.


## E. What earlier steps built (use, do not re-implement)
- `backend/` uv project (Python 3.12). `cane.contracts.awareness_event` — `ObjectClass`, `RangeBand`, `Source`, `AwarenessEvent`, `parse_awareness_event`. `cane.transport` — not used by the cloud (events route cloud → laptop → phone; the cloud only answers HTTP).
- `contracts/README.md` carries the COCO → 12-class mapping table (`bicycle → cyclist`, `motorcycle → scooter`, `truck → car`, others → `unknown_obstacle` or dropped) and the `range_band` metre boundaries (near ≤ 5 m, mid 5–15 m, far > 15 m).

## F. Controller rulings — binding (the architect may amend; amendments land in task-7-design.md)

### F1. Model and weights (closes build-plan §6 open question 3)
Ruling: **Ultralytics YOLO11s, COCO-pretrained**, is the single weights file for BOTH the phone (CoreML export, step 4) and the cloud (TensorRT export, this step). Same weights ⇒ same raw class list ⇒ one `class_map.py`. Input 640 for rungs 1–2, 512 for rungs 3–4 (export both engines, or one dynamic-shape engine — architect decides). Confidence threshold 0.35 at the detector, then the contract's confidence travels unchanged.

### F2. Package layout `backend/src/cane/cloud/`
```
cloud/
  __init__.py
  api_models.py        # from §D (create if step 6 has not yet)
  app.py               # FastAPI: POST /infer (multipart), GET /healthz, GET /warmup; no clock reads in the request path
  class_map.py         # COCO name -> ObjectClass | None (drop), the table from contracts/README.md; typical object heights per class
  detector/base.py     # RawDetection(x1,y1,x2,y2 px, coco_name, confidence); Protocol FrameDetector.detect(images: list[np.ndarray]) -> list[list[RawDetection]]
  detector/fake.py     # deterministic scripted detector for tests (returns pre-programmed boxes per call)
  detector/tensorrt_yolo.py   # ultralytics YOLO(".engine") batched over 6 crops; import guarded; only constructed when CANE_DETECTOR=tensorrt
  tracker.py           # per (session_id, crop_index) supervision.ByteTrack; state evicted after TRACK_IDLE_EVICT_S = 5
  motion.py            # pure: box height -> range estimate -> RangeBand; height growth over echoed t_capture_ms deltas -> closing_mps (EMA smoothed)
  service.py           # InferenceService.infer(meta, images) -> InferenceResponse: detect -> map classes -> track -> motion -> Detection list
  settings.py          # pydantic-settings: CANE_DETECTOR (fake|tensorrt), engine path, conf threshold, port
docker/
  Dockerfile           # base: nvcr.io/nvidia/pytorch or ultralytics GPU image; installs backend with the `gpu` extra
  entrypoint.sh        # if no engine at $ENGINE_PATH: export YOLO11s -> TensorRT ON THIS GPU (fp16), cache to the mounted volume; then uvicorn
  build_and_push.sh    # docker build + push to Artifact Registry (us-east1)
deploy/
  create_l4_vm.sh      # gcloud compute instances create ... --zone us-east1-b --accelerator type=nvidia-l4,count=1 --image-family ... , firewall rule for the port, startup script runs the container with --gpus all and a persistent volume
  warmup_loop.sh       # curl /warmup every 20 s from the laptop — "never let the demo be the cloud's first request"
  README.md            # 40 lines: build, deploy, smoke test on the L4 (`python -m cane.cloud.smoke --url ...`), cold start expectations, region choice
```
Add deps: `fastapi`, `uvicorn[standard]`, `python-multipart`, `supervision`, `pydantic-settings`, `numpy`, `opencv-python-headless`; optional extra `gpu = ["ultralytics>=8.3", "torch", "tensorrt"]` NOT installed in the dev env. The test suite never imports ultralytics/torch.

### F3. Timeless cloud (tested)
- `t_capture_ms` in the response is the request's value, byte-identical. The request path contains no `time.time()`/`datetime.now()` — `inference_ms` is measured with `time.perf_counter()` (a duration, not a timestamp). A test monkeypatches `time.time` to raise and asserts `/infer` still succeeds.
- Motion deltas use the echoed `t_capture_ms` of consecutive requests in the same session (laptop-stamped, phone-clock terms), never a cloud clock. If Δt ≤ 0 or > 2000 ms, `closing_mps` is `null` for that frame.

### F4. Motion math (`motion.py`, pure, unit-tested)
- Pinhole range from box height: `range_m = f_px * H_class / h_px` with `f_px = (crop_px / 2) / tan(30°)` and `H_class` typical heights (person 1.7, cyclist 1.8, scooter 1.6, car 1.5, bus 3.0, pole 3.0, bollard 0.9, wall 2.5, parked_car 1.5, door 2.1, overhang 2.5, unknown_obstacle 1.5 — constants in `class_map.py`). Band per §E boundaries. **Never emit `range_m`** — the estimate is only used to pick the band and to compute closing speed.
- `closing_mps = -(range_t - range_{t-1}) / Δt`, EMA with α = 0.5 over the track; positive = approaching; clamp to [-60, 60]; `null` until the track has two samples. Producers must satisfy the contract's `closing_mps` bounds.
- `track_id` = `f"{crop_index}:{bytetrack_id}"` — stable per crop (a seam-crossing object gets a new id; accepted).
- `cx, cy, w, h` normalized to the crop; `confidence` unchanged; `cls` mapped; detections whose COCO name maps to `None` are dropped.

### F5. Engine build on the target GPU (acceptance "the engine loads on a fresh L4 boot")
- Engines are not portable: `entrypoint.sh` exports the engine at container start if `$ENGINE_PATH` is missing, on the GPU it will serve on, and caches it on a persistent volume so subsequent boots skip the ~3–5 min export. `/healthz` reports `{"detector": "tensorrt", "engine_ready": true, "gpu": "<name>"}`. `/warmup` runs one batched inference on zeros and returns `inference_ms`.
- `python -m cane.cloud.smoke --url http://<vm>:8000` posts six JPEGs from a bundled test image, prints `inference_ms` and the detections, and exits non-zero if `inference_ms > 25` or `engine_ready` is false. This is the team's spike-7 tool.

### F6. Tests (`backend/tests/cloud/`, TDD, no GPU, `-W error` clean)
- `test_class_map.py`: every COCO hazard name maps per the README table; unknown names → `None`; every `ObjectClass` has a height.
- `test_motion.py`: known geometry → band boundaries (heights that put a person at 4.9/5.1/14.9/15.1 m); a box growing at a rate equivalent to 6.7 m/s closing over 3 frames → `closing_mps` within 10 %; receding → negative; Δt out of range → null; EMA smoothing; clamping.
- `test_tracker.py`: with the fake detector emitting the same slowly-moving box for 30 frames, the `track_id` is identical across all 30 (acceptance "track_id stable across 30 frames"); two crops keep independent ids; idle eviction.
- `test_app.py` (httpx `AsyncClient` over the ASGI app, fake detector): multipart request with `meta` + `crop_0..crop_5` → valid `InferenceResponse`; `t_capture_ms` echoed; timeless (monkeypatched `time.time` raising); no `range_m` anywhere in the response (the model has no such field — assert on the raw JSON); malformed meta → 422; missing crop → 422; `/healthz`, `/warmup` shapes; non-inference overhead with the fake detector < 5 ms p50 over 50 requests.
- `test_service_contract.py`: a full response converted by a small helper into `AwarenessEvent(source=x4_cloud, ...)` (the bridge's job in step 6 — here only to prove the numbers fit the contract) passes `parse_awareness_event`.

### F7. Out of scope
The bridge (6), rung stepping and local CoreML (8), Firestore (15), ADK (18), OCR/faces.

## G. Global constraints (verbatim)
- "Build the TensorRT engine ON the target L4 inside the container build — engines are not portable across GPU architectures. The cloud is timeless: echo t_capture and never read the local clock."
- "Acceptance: the engine loads on a fresh L4 boot; inference under 25 ms; track_id stable across 30 frames; no metres ever emitted."
- "Cold start is 30–60 s+. Never let the demo be the first request — run a warm-up ping loop."
- Commit format `<type>: <description>`; no attribution trailers. Immutable data; small files; explicit error handling; constants not magic numbers.
