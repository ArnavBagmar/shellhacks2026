# Task 7 design review — Cloud L4 TensorRT + ByteTrack + closing speed (ecc:architect, opus)

2026-09-26 · Read: `docs/steps/step-07-brief.md`; `docs/steps/step-06-brief.md` §F1, §F4; `contracts/awareness_event.schema.json`; `contracts/README.md`; `backend/src/cane/contracts/awareness_event.py`; `backend/pyproject.toml`; `backend/uv.lock` (resolved versions); `docs/steps/step-09-design.md` §1–2 (format) and §4 (hazards); `docs/camera-sync-design.md` §3, §4.6; `backend/src/cane/transport/wall_clock.py`. At review time no `cane/bridge/` or `cane/cloud/` existed — whichever of step 6 / step 7 lands first creates `api_models.py` in the A2 shape.

## 1. Verdict — ACCEPT WITH AMENDMENTS

F1–F7 are structurally right: the timeless rule, the per-crop tracker, box-height-only range, the fake detector, and the never-emit-metres contract all hold. Fifteen amendments A1–A15 are binding; where one conflicts with section F of `step-07-brief.md`, the amendment wins.

Six defect classes in F. (1) **The dependency facts have moved under the brief**: the lock resolves `supervision 0.30.5` (not 0.25), `ultralytics 8.4.163` (not 8.3), and `tensorrt 11.3.0.99` (CUDA 13, **sdist-only, no marker**) — `uv sync --extra gpu` on this Mac tries to build TensorRT and fails, and installing that extra inside an NGC image would clobber the image's TRT. (2) **The eviction math in F2/Q3 conflates two timers**; supervision's `max_time_lost = int(frame_rate/30 * lost_track_buffer)` makes `lost_track_buffer` a *wall-duration* knob only when `frame_rate` is the real rung fps — left at the default it gives a 10 s zombie buffer at 3 fps. (3) **ByteTrack ids restart at 1 after eviction**, so a reused `"2:7"` can be swallowed by step 9's 8 s urgency-1 refractory — a silent no-announce. (4) **F4's motion math has three blow-up paths** the contract clamp hides rather than fixes: a vertically truncated box, a class flip on a live track, and a sub-10 px box each manufacture a fake ±60 m/s closing, which is exactly the number that drives urgency 3 on the phone. (5) **F6's "< 5 ms non-inference overhead"** is unachievable and wrong: six 640×640 `cv2.imdecode` calls alone cost 6–9 ms on an M2. (6) **Over-scope**: `detector/tensorrt_yolo.py` is redundant (one `ultralytics.YOLO` class loads both `.pt` and `.engine`) and `build_and_push.sh`/`create_l4_vm.sh` are unrunnable fiction today — no `gcloud`, no project.

Two facts to keep in mind. Step 7's *shippable* deliverable today is a local server + `CANE_DETECTOR=ultralytics` on MPS driving a real X4 end-to-end run; every GPU artefact is untested documentation until an account exists, and must be labelled as such. And the crop boundaries (±30, ±90, ±150, 180) are *exactly* step 9's `SEAM_BEARINGS_DEG`, so per-crop id churn is already covered by the phone's seam dedup — do not spend hours on cross-crop association.

## 2. Amendments (binding)

### A1 — Scope split: what ships today, what is labelled UNVERIFIED
Two tiers, and no reviewer may treat tier 2 as done.
- **Tier 1 (must run today, on this Mac):** `cane/cloud/{api_models,settings,class_map,motion,tracker,service,app,smoke}.py`, `cane/cloud/detector/{base,fake,factory,ultralytics_yolo}.py`, the full test suite green under `uv run pytest -q -W error`, and a live run `X4 → bridge → this service (CANE_DETECTOR=ultralytics, CANE_DEVICE=mps) → events → phone`.
- **Tier 2 (documentation, cannot be executed):** `docker/Dockerfile`, `docker/entrypoint.sh`, `cane/cloud/export_engine.py`, `deploy/create_l4_vm.sh`, `deploy/warmup_loop.sh`, `deploy/README.md`. **Every tier-2 file starts with the line** `# UNVERIFIED as of 2026-09-26: no GPU, no gcloud, no GCP project. Nothing here has been executed.` and `deploy/README.md` repeats it in the first paragraph. Acceptance criteria "the engine loads on a fresh L4 boot" and "inference under 25 ms" are **deferred**, verifiable only by `python -m cane.cloud.smoke` once a VM exists; say so in the build status.
- Implementer order (≈4 h): api_models → class_map → motion → tracker → detector/{base,fake} → service → app → tests → detector/ultralytics_yolo + factory → smoke → tier 2 in whatever time remains. **If time runs out, tier 2 is the thing that gets cut**, not tests.

### A2 — `api_models.py` (amends step 6 §F4 in four places)
`backend/src/cane/cloud/api_models.py`, all models `model_config = ConfigDict(extra="forbid", frozen=True, strict=True)`, plus `populate_by_name=True` on the two that carry the `schema` alias.

```python
SCHEMA_VERSION: Final = 1
CROP_COUNT: Final = 6
SESSION_ID_PATTERN: Final = r"^[A-Za-z0-9_.:-]{1,64}$"

class InferenceMeta(BaseModel):
    schema_version: Literal[1] = Field(alias="schema")          # A2.1
    t_capture_ms: Annotated[int, Field(ge=T_CAPTURE_MS_MIN, le=T_CAPTURE_MS_MAX)]  # A2.2
    rung: Annotated[int, Field(ge=1, le=4)]
    crop_px: Annotated[int, Field(ge=256, le=1280)]             # + validator: crop_px % 32 == 0
    yaws_deg: Annotated[tuple[Annotated[float, Field(strict=False)], ...],
                        Field(min_length=6, max_length=6)]      # A2.3
    session_id: Annotated[str, Field(pattern=SESSION_ID_PATTERN)]

class Detection(BaseModel):
    crop_index: Annotated[int, Field(ge=0, le=5)]
    cx: Annotated[float, Field(ge=0.0, le=1.0, allow_inf_nan=False)]   # cy, w, h identical
    cls: Annotated[ObjectClass, Field(strict=False)]
    confidence: Annotated[float, Field(ge=CONFIDENCE_MIN, le=CONFIDENCE_MAX, allow_inf_nan=False)]
    track_id: Annotated[str, Field(min_length=TRACK_ID_MIN_LENGTH, max_length=TRACK_ID_MAX_LENGTH)]
    closing_mps: Annotated[float | None, Field(ge=CLOSING_MPS_MIN, le=CLOSING_MPS_MAX, allow_inf_nan=False)]
    range_band: Annotated[RangeBand | None, Field(strict=False)]
    # NO range_m. NO bearing_deg. NO urgency. NO ttl_ms.

class InferenceResponse(BaseModel):
    schema_version: Literal[1] = Field(alias="schema")
    t_capture_ms: int
    inference_ms: Annotated[float, Field(ge=0.0, allow_inf_nan=False)]
    detections: tuple[Detection, ...]
```
Four deltas from step-06 §F4, all binding on step 6 too:
- **A2.1** The field is `schema_version` with `alias="schema"`. A field literally named `schema` raises `NameError: Field name "schema" shadows an attribute in parent "BaseModel"` in pydantic v2 — `AwarenessEvent` already solved it this way. FastAPI serialises `response_model` with `by_alias=True` by default, so the wire key stays `"schema"`; any hand-rolled `JSONResponse` must pass `by_alias=True`.
- **A2.2** Every numeric bound is **imported** from `cane.contracts.awareness_event` (`T_CAPTURE_MS_MIN/MAX`, `CONFIDENCE_*`, `CLOSING_MPS_*`, `TRACK_ID_*`), never retyped. A stamp outside the contract window is a **422 at the cloud boundary**, not an unbuildable event at the bridge.
- **A2.3** `yaws_deg` items are `strict=False` so a bridge that serialises `[0, 60, 120, 180, -120, -60]` as ints is not 422'd. The cloud **validates and ignores** `yaws_deg` — bearing is the bridge's job.
- **A2.4** Sequence fields are `tuple[...]`, not `list[...]` (frozen model, deep immutability). Construct with `tuple(...)` in Python; JSON arrays validate fine.

### A3 — `settings.py`: exact env vars, and the one naming trap
`pydantic-settings`, `SettingsConfigDict(env_prefix="CANE_", frozen=True, extra="ignore")`. **No field may start with `model_`** (`model_path` emits pydantic's `protected_namespaces` `UserWarning`, which is a hard failure under `-W error`) — hence `weights_path`.

| field | env | default | note |
|---|---|---|---|
| `detector` | `CANE_DETECTOR` | `"fake"` | `Literal["fake","ultralytics","tensorrt"]` |
| `weights_path` | `CANE_WEIGHTS_PATH` | `Path("yolo11s.pt")` | `.pt` for `ultralytics` |
| `engine_path` | `CANE_ENGINE_PATH` | `Path("/models/yolo11s_b12dyn_fp16.engine")` | `.engine` for `tensorrt` |
| `device` | `CANE_DEVICE` | `"cpu"` | `"mps"` locally, `"0"` in the container |
| `conf_detect` | `CANE_CONF_DETECT` | `0.15` | fed to YOLO (A8) |
| `conf_emit` | `CANE_CONF_EMIT` | `0.35` | contract threshold, F1 preserved |
| `iou` | `CANE_IOU` | `0.45` | NMS |
| `max_det_per_crop` | `CANE_MAX_DET_PER_CROP` | `20` | caps response size |
| `max_crop_bytes` | `CANE_MAX_CROP_BYTES` | `2_000_000` | 413 above |
| `max_sessions` | `CANE_MAX_SESSIONS` | `8` | registry LRU cap |
| `api_key` | `CANE_API_KEY` | `""` | empty = no auth |
| `port` | `CANE_PORT` | `8000` | |
| `log_level` | `CANE_LOG_LEVEL` | `"INFO"` | |

The entrypoint and the local run use the **same** names — no `ENGINE_PATH`/`CANE_ENGINE_PATH` split.

### A4 — One detector file, not two; extras markered; a separate venv for torch (Q2: ACCEPT, amended)
`ultralytics.YOLO(path)` loads a `.pt` and a `.engine` through the identical `predict` API. **Delete `detector/tensorrt_yolo.py` from F2.** Replace with:

```python
# detector/base.py — zero heavy imports
@dataclass(frozen=True, slots=True)
class RawDetection:
    x1: float; y1: float; x2: float; y2: float   # pixels, in the crop's own frame
    coco_name: str
    confidence: float

class FrameDetector(Protocol):
    name: str                      # "fake" | "ultralytics" | "tensorrt"
    engine_ready: bool
    gpu_name: str | None
    def detect(self, images: Sequence[NDArray[np.uint8]]) -> tuple[tuple[RawDetection, ...], ...]: ...

# detector/ultralytics_yolo.py — the ONLY module importing ultralytics/torch
class UltralyticsDetector:
    def __init__(self, weights: Path, device: str, conf: float, iou: float,
                 max_det: int, half: bool, label: str) -> None: ...   # `from ultralytics import YOLO` INSIDE __init__

# detector/factory.py
def build_detector(s: CloudSettings) -> FrameDetector: ...   # imports the ultralytics module lazily, inside the branch
```
- `CANE_DETECTOR=ultralytics` → `UltralyticsDetector(weights=s.weights_path, device=s.device, half=False, label="ultralytics")`; `CANE_DETECTOR=tensorrt` → same class with `weights=s.engine_path, device="0", half=True, label="tensorrt"`. `engine_ready = weights.suffix == ".engine" and weights.exists()`.
- `predict` call: `self._model.predict(list(images), imgsz=images[0].shape[0], conf=self._conf, iou=self._iou, max_det=self._max_det, device=self._device, half=self._half, verbose=False)`. Numpy input must be **BGR HWC uint8** (what `cv2.imdecode` returns). Read boxes from `r.boxes.xyxy` (already in original-image pixels) and names from `self._model.names[int(cls_idx)]` — **map by COCO name, never by index**.
- **Extras (fix `pyproject.toml`):** `yolo = ["ultralytics>=8.3"]` is right. `gpu` is **wrong as locked** — `tensorrt` resolved to `11.3.0.99`, sdist-only, CUDA 13, **no platform marker**, so `uv sync --extra gpu` on darwin tries to build it. Change to
  `gpu = ["ultralytics>=8.3; sys_platform == 'linux'", "tensorrt; sys_platform == 'linux'"]` and add the comment `# not used by docker/Dockerfile — the NGC image supplies torch and TensorRT (A11)`.
- **The test suite stays torch-free by absence, not discipline:** never install `--extra yolo` into `.venv`. Local run:
  `UV_PROJECT_ENVIRONMENT=.venv-yolo uv run --extra yolo uvicorn cane.cloud.app:app --host 127.0.0.1 --port 8000`
  with `CANE_DETECTOR=ultralytics CANE_DEVICE=mps CANE_WEIGHTS_PATH=yolo11s.pt PYTORCH_ENABLE_MPS_FALLBACK=1`. Add `.venv-yolo/` to `.gitignore`. Tests run in the default `.venv`, where `import torch` cannot succeed.
- Add `tests/cloud/test_no_heavy_imports.py::test_importing_the_app_does_not_import_torch_or_ultralytics` asserting `{"torch","ultralytics","torchvision"} & set(sys.modules) == set()` after `import cane.cloud.app` **and** after a full fake-detector `/infer` — it must be a `sys.modules` assertion so it still means something inside `.venv-yolo`.
- First MPS inference compiles graphs for 5–10 s: `/warmup` is mandatory locally too, not just on the L4.

### A5 — `class_map.py`: an explicit keep-list and per-COCO heights (F4's per-class heights are not enough)
`contracts/README.md`'s "anything else → `unknown_obstacle`" governs classes we choose to *emit*; a producer may also drop a detection entirely, which the contract does not constrain. Two tables:

```python
COCO_TO_CLASS: Final[Mapping[str, ObjectClass]] = {        # anything absent -> dropped
  "person": PERSON, "bicycle": CYCLIST, "motorcycle": SCOOTER, "car": CAR,
  "truck": CAR, "bus": BUS, "train": BUS, "fire hydrant": BOLLARD,
  "stop sign": POLE, "parking meter": POLE, "bench": UNKNOWN_OBSTACLE,
  "dog": UNKNOWN_OBSTACLE, "potted plant": UNKNOWN_OBSTACLE,
  "chair": UNKNOWN_OBSTACLE, "suitcase": UNKNOWN_OBSTACLE,
}
HEIGHT_M_BY_CLASS: Final[Mapping[ObjectClass, float]] = {   # F4's table, unchanged; total over the 12
  PERSON:1.7, CYCLIST:1.8, SCOOTER:1.6, CAR:1.5, BUS:3.0, POLE:3.0, BOLLARD:0.9,
  WALL:2.5, PARKED_CAR:1.5, DOOR:2.1, OVERHANG:2.5, UNKNOWN_OBSTACLE:1.5,
}
HEIGHT_M_BY_COCO: Final[Mapping[str, float]] = {            # overrides: the BOX height, not the class ideal
  "truck":2.5, "train":3.5, "stop sign":0.75, "parking meter":1.2,
  "fire hydrant":0.8, "bench":0.5, "dog":0.6, "potted plant":0.7,
  "chair":0.9, "suitcase":0.6,
}
def map_coco(name: str) -> ObjectClass | None: ...
def height_m(coco_name: str, cls: ObjectClass) -> float:     # COCO override, else class default
```
Rationale for the override table: a truck mapped to `car` with H=1.5 reports ~1.7× its true range; a stop sign scored at pole's H=3.0 is 4× too far and would never warn. `traffic light` is **deliberately dropped** (the box is the overhead lamp housing, never a walkable hazard) — put that in a comment so nobody "fixes" it. `map_coco` is case-insensitive on a `.strip().lower()` key.

### A6 — `motion.py`: exact pure API, and the three blow-up paths F4 misses (Q4)
```python
FOV_DEG: Final = 60.0;  NEAR_MAX_M: Final = 5.0;  MID_MAX_M: Final = 15.0
MIN_BOX_H_PX: Final = 8.0;  EDGE_EPS_PX: Final = 2.0
MIN_DT_MS: Final = 1;  MAX_DT_MS: Final = 2000
EMA_ALPHA: Final = 0.5;  RAW_REJECT_MPS: Final = 40.0

def focal_px(crop_px: int) -> float:            # (crop_px/2)/tan(radians(30)) -> 554.256 @640, 443.405 @512
def range_m_from_height(h_px: float, height_m: float, crop_px: int) -> float | None
def band_for_range(range_m: float) -> RangeBand # <=5.0 NEAR ; <=15.0 MID ; else FAR  (both boundaries inclusive-below)

@dataclass(frozen=True, slots=True)
class MotionState:
    last_range_m: float
    last_t_capture_ms: int
    height_m: float
    ema_mps: float | None

def update_motion(state: MotionState | None, range_m: float, height_m: float,
                  t_capture_ms: int) -> tuple[MotionState, float | None]
```
Binding rules:
1. **Sign / formula**: `raw = (state.last_range_m - range_m) / (dt_ms/1000.0)`; positive = approaching, matching the contract. EMA: **first accepted sample initialises `ema = raw`** (never blends against 0, which would halve the first, most-important reading); afterwards `ema = 0.5*raw + 0.5*ema_prev`. Returned value is the EMA, `round`ed to 3 dp, clamped to `[CLOSING_MPS_MIN, CLOSING_MPS_MAX]` imported from the contract module.
2. **First frame of a track**: `state is None` → return `(MotionState(range_m, t, height_m, None), None)`. `closing_mps` is `null` until the second frame, as F4 says.
3. **New track id** (ByteTrack assigned a fresh id): no state exists, so rule 2 applies automatically. **Class flip on a live id** (`height_m != state.height_m`): the range series is discontinuous by construction → return `(MotionState(range_m, t, height_m, None), None)` — baseline replaced, EMA cleared. *This is the fix for a person→cyclist flip manufacturing a 40 m/s closing.*
4. **Δt guards**: `dt_ms <= 0` → return `(state, None)` **unchanged** (an out-of-order or duplicate stamp must never rewind the baseline). `dt_ms > MAX_DT_MS` → return `(MotionState(range_m, t, height_m, None), None)` (baseline restarted, EMA cleared) — without the restart a 3 s gap poisons every later Δt.
5. **Glitch rejection**: `abs(raw) > RAW_REJECT_MPS (40)` → return `(MotionState(range_m, t, height_m, None), None)`. The ±60 contract clamp *hides* a geometry glitch as a plausible 60 m/s closing, which is precisely the value that trips step 9's urgency 3. Reject, do not clamp.
6. **Degenerate and truncated boxes** (enforced in `service.py`, not `motion.py`): `h_px <= 0` → drop the detection; `h_px < MIN_BOX_H_PX (8)` → emit `range_band=FAR`, **no motion sample** (state untouched); **vertically truncated** (`y1 <= EDGE_EPS_PX or y2 >= crop_px - EDGE_EPS_PX`) → emit the band best-effort but **no motion sample** and `closing_mps=None` for that frame. A box growing into the crop edge is the single largest source of fake closing speed at 60° crops.
7. **Producers satisfy the contract**: the returned value is finite, within ±60, and `None` is legal for `closing_mps`. `range_band` is never `None` for an emitted detection (so `x4_cloud` always carries a band, per §4.6); `range_m` is never returned to a caller at all — `range_m_from_height` is module-private in effect and its result dies inside `service.py`.
8. **Free property worth a test**: range is invariant to `crop_px`, because `f_px` and `h_px` both scale linearly — a mid-session rung step (640→512) must not jump the band.

### A7 — `tracker.py`: supervision adapter, corrected eviction math, generation-stamped ids (Q3: ACCEPT, amended)
**Version reality check first.** The lock resolves **`supervision 0.30.5`**, not 0.25. Before writing a line, run and paste the output into the report:
```
uv run python -c "import supervision as sv, inspect; print(sv.__version__); \
print(inspect.signature(sv.ByteTrack.__init__)); \
print(inspect.signature(sv.ByteTrack.update_with_detections)); \
print(inspect.signature(sv.Detections.__init__))"
```
Expected (0.25–0.30): `ByteTrack(track_activation_threshold=0.25, lost_track_buffer=30, minimum_matching_threshold=0.8, frame_rate=30, minimum_consecutive_frames=1)`. **If any kwarg is renamed** (pre-0.19 used `track_thresh`/`match_thresh`; a future release may rename again), fix it *only* inside `CropTracker` and note it. Also pin `supervision>=0.25,<0.31` in `pyproject.toml` and run `uv sync --frozen` for the rest of the hackathon — no re-locking at hour 30.

Four API facts the implementer must handle, each of which silently corrupts output if missed:
- `detections2boxes` requires **both** `confidence` and `class_id` to be non-`None`; passing either as `None` raises.
- `update_with_detections` **mutates the Detections you pass** (sets `.tracker_id`) and **returns a filtered subset** — detections that matched no track are dropped. Never reuse an input `Detections`, and never zip the output with your raw list by position. Carry the mapping explicitly: `sv.Detections(xyxy=..., confidence=..., class_id=..., data={"det_index": np.arange(n, dtype=int)})`, then read `tracked.data["det_index"]`. A test must prove the `data` round-trip survives, because it is the one thing a version bump would break quietly.
- **A crop with zero detections must still call `update`** (with `sv.Detections.empty()`); skipping it freezes the internal frame counter and lost tracks never age out.
- `minimum_consecutive_frames` stays **1**. Raising it to 2 costs 333–500 ms before a cyclist's first announcement — latency is the product.

**Eviction math (the Q3 fix).** Supervision computes `max_time_lost = int(frame_rate / 30.0 * lost_track_buffer)`, so `lost_track_buffer` is a duration knob (`lost_track_buffer/30` seconds) **only if `frame_rate` is the real rung fps**. Left at the default 30 while the rung runs at 3 fps, a buffer of 30 becomes 30 frames = **10 s** of zombie tracks. Binding values:
```python
RUNG_FPS: Final[Mapping[int, int]] = {1: 3, 2: 2, 3: 3, 4: 2}   # docs/camera-sync-design.md §3.2
DEFAULT_FPS: Final = 3
LOST_TRACK_S: Final = 2.0
LOST_TRACK_BUFFER: Final = int(LOST_TRACK_S * 30)               # 60 -> 6 frames @3fps, 4 @2fps, both 2.0 s
MATCH_THRESHOLD: Final = 0.9                                     # cost = 1-IoU, so accepts IoU > 0.1
TRACK_IDLE_EVICT_MS: Final = 5000
sv.ByteTrack(track_activation_threshold=conf_emit, lost_track_buffer=LOST_TRACK_BUFFER,
             minimum_matching_threshold=MATCH_THRESHOLD, frame_rate=RUNG_FPS.get(rung, DEFAULT_FPS),
             minimum_consecutive_frames=1)
```
`MATCH_THRESHOLD` is loosened from the 0.8 default because ByteTrack's Kalman filter assumes ~30 fps; at 2–3 fps a crossing cyclist's boxes barely overlap frame to frame, and the default rejects the match. **Test the lost buffer behaviourally** (id survives 6 empty frames at fps=3, is gone by the 8th), never by asserting `max_time_lost` — that keeps the test version-proof.

`TRACK_IDLE_EVICT_MS` is a **different** timer: it evicts the whole `(session_id, crop_index)` entry (tracker + motion dict) and is measured in **echoed `t_capture_ms` deltas, never a clock** (A9). Sweep on every `/infer`.

**Generation-stamped track ids.** A new `ByteTrack` restarts ids at 1, so `"2:7"` can name a different physical object ~5 s later — and step 9's `refractory(1) = 8000 ms` would then silently suppress it. Amend F4:
```
track_id = f"{crop_index}:{gen}:{bytetrack_id}"      # e.g. "3:12:7"
```
where `gen` comes from a **process-wide** `itertools.count()`, incremented each time a `CropTracker` is constructed. Ids never repeat within a process; length stays far under the contract's 64. Step 6 still uses the id **as given**, unprefixed — step-06 §F4 is otherwise unchanged.

**Rung changes mid-session**: `frame_rate` is fixed at first construction of a `(session, crop)` entry. A later rung change is logged once and **ignored** — rebuilding the tracker would churn every id on the crop, and the only cost of not rebuilding is a ~1 s difference in lost-track tolerance. Id stability wins.

```python
@dataclass(frozen=True, slots=True)
class TrackedBox: det_index: int; track_id: str

class CropTracker:            # the ONLY module that imports supervision
    def __init__(self, fps: int, crop_index: int, gen: int) -> None: ...
    def update(self, xyxy: NDArray[np.float32], conf: NDArray[np.float32],
               class_ids: NDArray[np.int32]) -> tuple[TrackedBox, ...]: ...

class TrackerRegistry:
    def __init__(self, max_sessions: int = 8, idle_evict_ms: int = TRACK_IDLE_EVICT_MS) -> None: ...
    def entry_for(self, session_id: str, crop_index: int, rung: int, t_capture_ms: int) -> SessionCrop: ...
    def sweep(self, t_capture_ms: int) -> int: ...     # returns entries evicted
    @property
    def session_count(self) -> int: ...
```
`SessionCrop` holds `tracker: CropTracker`, `motion: dict[str, MotionState]`, `last_t_capture_ms: int`. Sessions are capped at `max_sessions` (LRU by `last_t_capture_ms`) — an unbounded dict keyed on a client-supplied string is a memory leak on an internet-facing GPU VM. Motion entries are pruned in the same sweep.

**Escape hatch (do not build unless needed):** if supervision 0.30.5's `ByteTrack` turns out to be renamed or broken, a greedy IoU + centroid matcher with a 2 s lost buffer is ~60 lines and genuinely adequate at 2–3 fps. The whole point of the `CropTracker` boundary is that this swap touches one file.

### A8 — `service.py`: pipeline order, the detect/emit confidence split, and no FastAPI import
```python
class InferenceService:                     # imports NOTHING from fastapi
    def __init__(self, detector: FrameDetector, registry: TrackerRegistry, settings: CloudSettings) -> None
    def infer(self, meta: InferenceMeta, images: Sequence[NDArray[np.uint8]]) -> InferenceResponse   # sync
```
**`service.py` must not import `fastapi`.** Step 8 runs this exact class in-process on the laptop with a local detector; that constraint turns step 8's fallback into a one-hour job instead of a reimplementation. Same for `tracker.py`, `motion.py`, `class_map.py` (numpy + supervision only).

Per-crop order: `detect → map class (drop None) → clamp/validate box → ByteTrack update → range/band → motion → Detection`.

**Confidence split (amends F1).** F1's 0.35 is preserved as the *emit* threshold, and a lower *detect* floor is added:
- YOLO runs at `conf_detect = 0.15`; `ByteTrack(track_activation_threshold = conf_emit = 0.35)`.
- A detection is **emitted** only if `confidence >= conf_emit`.
This is the "BYTE" in ByteTrack: 0.15–0.35 boxes cannot start a track but can keep an existing one alive through a blurry or partly occluded frame — which is exactly the frame where an overtaking cyclist's id would otherwise churn. The wire confidence is still the detector's value, unchanged. Compare against the threshold **before** the float32→float64 conversion, and never assert exact equality on it in tests.

Other binding details: boxes are normalised by the **actual decoded image shape**, not by `meta.crop_px` (defence against a lying meta), then clamped into `[0,1]`; `w,h <= 0` → drop. `inference_ms` is measured with `time.perf_counter()` around the *entire* service call — **decode included** — because that is the number step 8's health monitor and step 16's bench must trust; log `decode_ms` and `detect_ms` separately at DEBUG. `MAX_DETECTIONS_PER_CROP = 20` after confidence sort, so a crowd cannot produce a 2 MB response.

### A9 — Timeless: prove it statically, then demonstrate it dynamically (Q5, F3)
Two tests, because the monkeypatch test only covers the lines it happens to execute.
1. **`test_no_clock_reads.py::test_request_path_modules_contain_no_wall_clock_reads`** — AST-walk the source of an explicit module list and fail on any `time.time`, `time.time_ns`, `time.clock_gettime`, `datetime.*`, `date.today`, `*.fromtimestamp`, or any import of `cane.transport.wall_clock`. Allowed: `time.perf_counter`, `time.perf_counter_ns`, `time.monotonic`.
   `REQUEST_PATH_MODULES = ("api_models","app","class_map","motion","service","settings","tracker","detector/base","detector/fake","detector/factory","detector/ultralytics_yolo")`. `smoke.py` and `export_engine.py` are exempt.
2. **`test_app.py::test_request_path_survives_a_raising_time_time`** — monkeypatch `time.time`, `time.time_ns`, `datetime.datetime.now`, `datetime.datetime.utcnow` to raise, run a full `/infer`, assert 200 and a correct echo. **Wrap the strict window in `logging.disable(logging.CRITICAL)` and restore in a `finally`**, otherwise a single legitimate `logger.warning()` fails the test spuriously — `logging.LogRecord.__init__` calls `time.time()`.

`/healthz` also carries no `uptime` field, so the whole of `app.py` is clock-free and rule 1 can be stated without carve-outs.

### A10 — Timeless response and no metres, proven on the raw bytes (Q6: ACCEPT, confirmed)
`Detection` has no `range_m` field (A2) — confirmed, and the model-level `extra="forbid"` means one cannot be smuggled in. Three assertions:
- `test_detection_model_has_no_forbidden_fields`: `{"range_m","bearing_deg","urgency","ttl_ms"}.isdisjoint(Detection.model_fields)`.
- `test_response_json_mentions_no_metres_and_no_bearing`: assert on `response.text` (the raw body), not the parsed dict — `"range_m" not in response.text and "bearing" not in response.text`.
- `test_t_capture_ms_is_echoed_byte_identical`: send `1790123456789`; assert `resp.json()["t_capture_ms"] == 1790123456789` **and** the literal substring `'"t_capture_ms":1790123456789'` is in `response.text` (no `.0`, no exponent). Int round-trip is exact because both models type the field `int` under `strict=True` — a float or a numeric string is a 422, not a silent coercion.

### A11 — One dynamic engine, exported on the serving GPU, cached atomically with a GPU fingerprint (Q1: AMEND — one engine, not two)
**Decision: one dynamic-shape engine.** Ultralytics' TensorRT exporter builds an optimisation profile of `min=(1,3,32,32)`, `opt=(batch//2, 3, imgsz, imgsz)`, `max=(batch,3,imgsz,imgsz)`, so a single engine exported at `imgsz=640` covers every /32 shape from 32 to 640 — 512 and 640 both — and `AutoBackend` calls `set_input_shape` when the shape changes. Exporting at `batch=12` puts **`opt` exactly at our real batch of 6**. Pros: one 3–5 min export instead of two, one cache file, one sidecar, one warm path, and step 8 can step rungs without a model reload. Cons: a dynamic engine is ~10% slower than a static one (still far inside 25 ms for YOLO11s fp16 on an L4) and the first inference at each new shape pays a one-time context reshape — which is why `/warmup` warms **both** 640 and 512. Alternative considered and rejected: two static engines (marginally faster, doubles export time and the number of things that can be missing at 3 a.m. on a fresh boot). Documented escape hatch in `deploy/README.md`: if dynamic export fails on the L4, re-export `dynamic=False, batch=6, imgsz=640` and pin the bridge to rungs 1–2.

Exact export call (in `cane/cloud/export_engine.py`, imports guarded inside `main()`):
```python
from ultralytics import YOLO
YOLO(str(weights)).export(
    format="engine", imgsz=640, batch=12, dynamic=True, half=True,
    device=0, workspace=4, simplify=True, nms=False, verbose=False,
)
```
- `device=0` is **mandatory**: on CPU the exporter disables `half` and then asserts `not dynamic`.
- `workspace` is GiB in 8.3+; the lock has **ultralytics 8.4.163**, newer than the brief's 8.3 assumption — verify with `yolo export --help` on the L4 and drop the kwarg if it is rejected. CLI equivalent for a manual retry: `yolo export model=yolo11s.pt format=engine imgsz=640 batch=12 dynamic=True half=True device=0`.
- Confirm from the export log (or `trtexec --loadEngine=... --verbose`) that the profile's opt batch is 6 and the max spatial is 640.

**Caching (`entrypoint.sh` stays trivial; the logic is Python so it is reviewable):**
```sh
#!/usr/bin/env bash
# UNVERIFIED as of 2026-09-26: ...
set -euo pipefail
python -m cane.cloud.export_engine --weights "${CANE_WEIGHTS_PATH:?}" --out "${CANE_ENGINE_PATH:?}"
exec uvicorn cane.cloud.app:app --host 0.0.0.0 --port "${CANE_PORT:-8000}" \
     --workers 1 --timeout-keep-alive 30 --log-level "${CANE_LOG_LEVEL:-info}"
```
`export_engine.py` rules: (a) skip if `$CANE_ENGINE_PATH` exists **and** its sidecar `${CANE_ENGINE_PATH}.json` records the same `gpu_name`, `trt_version`, `ultralytics_version`, `imgsz`, `batch` — a persistent volume re-attached to a different GPU type must trigger a rebuild, which is the literal acceptance criterion "loads on a fresh L4 boot"; (b) export to `${out}.tmp` and `os.replace` into place, so a crashed or OOM-killed export never leaves a truncated engine that fails to deserialize; (c) write the sidecar only after the replace; (d) `--force` flag; (e) non-zero exit on failure so the container dies loudly and the VM restart policy retries.

**Dockerfile (tier 2):** base `nvcr.io/nvidia/pytorch:<tag>` (torch + TensorRT preinstalled). **Do not install the `gpu` extra** — PyPI `tensorrt` 11.3/cu13 would install a second, conflicting TensorRT over the image's. Install the project's base deps plus `pip install --no-deps ultralytics`. Add `apt-get install -y --no-install-recommends libgl1 libglib2.0-0` (the project depends on `opencv-python`, not `-headless`, because the bridge needs the GUI build on the Mac — one apt line is cheaper than splitting the dependency). Bake the weights into the image (`RUN python -c "from ultralytics import YOLO; YOLO('yolo11s.pt')"`) so a fresh boot never depends on a GitHub asset download, set `ENV YOLO_CONFIG_DIR=/models/ultralytics` and run `yolo settings sync=False` at build time, and `ENV CANE_DETECTOR=tensorrt CANE_DEVICE=0`.

### A12 — Concurrency: `asyncio.Lock` + `run_in_threadpool`, and exactly one worker (Q5)
Both, for different reasons, and both are load-bearing.
- **One worker is a correctness requirement, not a tuning choice**: `--workers 1` in the entrypoint. Two worker processes split the tracker registry, so the same physical object gets different ids on alternating frames and `track_id` stability — the whole point of this step — dies silently.
- **The lock is a correctness requirement too**: the bridge guarantees one request in flight, but `/warmup` (the 20 s loop from A13), the smoke tool, and step 8's health poll can all overlap it. TensorRT execution contexts are not thread-safe and the registry is mutable. Starlette's threadpool would happily run two handlers concurrently.

```python
@dataclass
class AppState:                      # built once by get_app_state(); tests override via dependency_overrides
    service: InferenceService
    lock: asyncio.Lock
    last_inference_ms: float | None

@app.post("/infer", response_model=InferenceResponse)
async def infer(meta: Annotated[str, Form()],
                crop_0: Annotated[UploadFile, File()], ..., crop_5: Annotated[UploadFile, File()],
                st: Annotated[AppState, Depends(get_app_state)]) -> InferenceResponse:
    ...                                       # validate + await file.read() here, in the event loop
    async with st.lock:
        return await run_in_threadpool(st.service.infer, parsed_meta, images)
```
The lock is an attribute of `AppState`, **not a module global** — a module-level `asyncio.Lock` shared across pytest event loops is a latent hang. `AppState` is built by a `functools.lru_cache`d `get_app_state()` used via `Depends`, **not** in a `lifespan` handler: `httpx.ASGITransport` does not run lifespan, so a lifespan-built detector would be `None` in every test. Tests inject the fake through `app.dependency_overrides[get_app_state]`.

Test: `test_concurrent_requests_are_serialized` — a fake detector that increments/decrements a counter and sleeps 20 ms; fire two `/infer` with `asyncio.gather`; assert the observed maximum concurrency is exactly 1.

### A13 — `app.py` boundary validation, limits, errors, and an optional key
Input validation at the boundary, all of it cheap:
- `meta` parsed with `InferenceMeta.model_validate_json(meta)`; `ValidationError` → **422** with a detail built from `exc.errors()` **locations only** — never echo the raw payload back (it is untrusted client data).
- A missing `crop_N` is a FastAPI 422 for free (six explicit parameters, not `list[UploadFile]` — the index matters).
- `len(raw) > settings.max_crop_bytes` → **413**. Without this, one 1 GB multipart part fills the VM's disk (`UploadFile` spools past 1 MB).
- `cv2.imdecode(...) is None` → **422** (`"crop_3 is not a decodable image"`).
- `img.shape != (meta.crop_px, meta.crop_px, 3)` → **422**. Never trust the meta to describe the bytes.
- If `settings.api_key` is non-empty, `/infer` and `/warmup` require header `X-Cane-Key`, compared with `hmac.compare_digest`; mismatch → **401**. `/healthz` is always open (step 8 polls it). Empty key = open, for local dev.
- A detector exception → **503** `{"detail": "inference unavailable"}` with the traceback logged server-side only. The bridge treats any non-200 as a dropped frame.

Endpoint shapes:
- `GET /healthz` → `{"detector": "fake|ultralytics|tensorrt", "engine_ready": bool, "gpu": str|null, "weights": str, "sessions": int, "last_inference_ms": float|null}`. No inference, no clock. `last_inference_ms` is what step 8's health monitor reads without paying for a probe.
- `GET /warmup?crop_px=` → runs `detector.detect` on zeros **bypassing the tracker** (no session state), under the same lock; default warms **both** 640 and 512 for the dynamic engine. Returns `{"engine_ready": bool, "warmed": [{"crop_px": 640, "inference_ms": 12.3}, {"crop_px": 512, "inference_ms": 9.1}]}`.

### A14 — Smoke tool and warm-up loop, both tiny (Q7: ACCEPT, shape fixed)
```
python -m cane.cloud.smoke --url http://HOST:8000 [--image PATH] [--crop-px 640]
                           [--frames 3] [--session smoke] [--key KEY] [--max-inference-ms 25.0]
```
Behaviour: `GET /healthz` → print `detector/engine_ready/gpu`; build six JPEGs (from `--image`, centre-cropped and resized to `crop_px`, posted as all six crops; if absent, a generated gradient); POST `--frames` times **350 ms apart** so ByteTrack reaches two samples and `closing_mps` actually appears; print a one-line table per detection (`crop cls conf track_id band closing`); exit non-zero if `engine_ready` is false, if any request is non-200, or if the **median of frames 2..N** exceeds `SMOKE_MAX_INFERENCE_MS = 25.0` (frame 1 pays the shape warm-up and is excluded). Keep the decision logic in a pure `evaluate(health, results, budget_ms) -> tuple[int, str]` and unit-test only that — running a live server inside pytest is out of scope.

`deploy/warmup_loop.sh`, ~12 lines: `URL=${1:?usage: warmup_loop.sh http://host:8000}`, `while true; do curl -fsS --max-time 10 "$URL/warmup" >/dev/null || echo "$(date -u) warmup failed"; sleep "${INTERVAL:-20}"; done`. It is the "never let the demo be the cloud's first request" insurance and it is also needed **locally**, because the first MPS inference compiles for 5–10 s.

### A15 — Cuts, rejections and corrections to F (Q8)
1. **Cut `detector/tensorrt_yolo.py`** — merged into `ultralytics_yolo.py` (A4). Saves a file and a divergent postprocessing path.
2. **Cut `docker/build_and_push.sh`** — its three commands live in `deploy/README.md`. An unrunnable script that looks like a deliverable is worse than a documented command.
3. **Reject F6's "non-inference overhead < 5 ms p50"** — six 640×640 `cv2.imdecode` calls are 6–9 ms on an M2 before any of our code runs; the budget is unreachable and the test would be deleted in frustration. Replace with `NON_INFERENCE_BUDGET_MS = 20.0` p50 over 50 requests with the fake detector, JPEGs **encoded once outside the loop**, and print the measured p50/p95 so the number is visible even when it passes.
4. **Correct `opencv-python-headless` in F2** — the lock already carries `opencv-python` (the bridge needs the GUI build for spike 4, and ultralytics depends on the same distribution). Keep one OpenCV, add `libgl1 libglib2.0-0` to the image (A11).
5. **`-W error` risk to verify before writing tests:** `uv run python -W error -c "import supervision"` and `... -c "import cane.cloud.app"`. supervision 0.30.5 pulls matplotlib, scipy, pillow and defusedxml. If it warns, add **only** narrow ignores to `[tool.pytest.ini_options]` (`filterwarnings = ["ignore::DeprecationWarning:supervision.*", "ignore::DeprecationWarning:matplotlib.*"]`) — ini filters take precedence over a command-line `-W error`, so never add a blanket `"ignore"`.
6. **`httpx 0.28.1` removed the `app=` shortcut** — tests must use `AsyncClient(transport=ASGITransport(app=app), base_url="http://test")`. `asyncio_mode = "auto"` is already configured, so tests are plain `async def`.
7. **Keep** `test_service_contract.py` (it is the "no metres ever emitted" proof end to end) and **keep** `GET /warmup` as a GET (curl-able from a phone on the venue wifi).
8. **`deploy/create_l4_vm.sh` must not default the firewall to `0.0.0.0/0`** — `ALLOWED_CIDR` is a required env var with no default (`: "${ALLOWED_CIDR:?set to the laptop's public /32}"`). An open port to a GPU VM that decodes attacker-supplied JPEGs is the one security hole this step can actually create.
9. **Do not re-lock.** `uv sync --frozen` for the remainder of the hackathon; `ultralytics>=8.3` already drifted to 8.4.163 and `tensorrt` to an unpinned 11.3.0.99.

## 3. Reviewer checklist (failure classes the code reviewers must look for)

**Emits a wrong number (the dangerous class).**
Truncated box feeding `update_motion` → A6.6; class flip on a live track id not resetting the baseline → A6.3; a `raw` sample clamped to ±60 instead of rejected → A6.5 (a clamped glitch is indistinguishable from a real car and scores urgency 3 on the phone); EMA initialised by blending against 0 → A6.1; `dt_ms <= 0` rewinding the baseline → A6.4; range computed from `meta.crop_px` instead of the decoded shape → A8; band boundary using `<` where the contract says `near <= 5` → A6; `cx/cy/w/h` unclamped → a pydantic 500 on `1.0001`.

**Announces nothing (silent failure).**
`lost_track_buffer` with a default `frame_rate` → 10 s zombie ids at 3 fps → A7; ByteTrack ids reused after eviction colliding with step 9's 8 s urgency-1 refractory → A7 generation counter; a crop with zero detections skipping `update_with_detections` → lost tracks never age → A7; `minimum_consecutive_frames > 1` → A7; zipping `update_with_detections` output positionally against the raw list (it returns a **filtered subset**) → A7 `data["det_index"]`; a COCO name silently dropped because the keep-list was matched case-sensitively → A5; `conf_detect` raised back to 0.35, removing the low-confidence association that keeps a blurry cyclist alive → A8.

**Leaks a clock or a metre.**
Any `time.time`/`datetime.now` in the eleven request-path modules → A9 AST test; the monkeypatch test passing only because the path it exercised never logged → A9 rule 2; `uptime` on `/healthz` → A13; a `range_m` field, or a metres value in a `detail` string → A10 raw-body assertion; `t_capture_ms` typed `float` or re-derived rather than echoed → A2/A10.

**Concurrency and resource.**
Module-level `asyncio.Lock` → A12; `--workers 2` → split tracker state → A12; missing `max_crop_bytes` check → A13; unbounded `sessions` dict → A7 `max_sessions`; `service.py` importing `fastapi` → blocks step 8 → A8.

**Build and deploy.**
`pip install` of the `gpu` extra inside the NGC image → two TensorRTs → A11; export without `device=0` → `half`+`dynamic` assertion → A11; a non-atomic engine write → a truncated `.engine` that fails to deserialize on the next boot → A11; no GPU fingerprint sidecar → a volume moved to a different accelerator serves an unloadable engine → A11; `--extra yolo` installed into `.venv` → the never-import-torch rule becomes unenforceable → A4.

## 4. Hazards for later steps

- **H1 (step 8, highest).** `service.py`, `tracker.py`, `motion.py` and `class_map.py` are deliberately FastAPI-free and torch-free. The local fallback must be a new `FrameDetector` plus `InferenceService` **in-process on the laptop**, not a reimplementation — otherwise `x4_local` and `x4_cloud` will disagree on bands and closing speed at exactly the moment the health monitor flips. Poll `GET /healthz` (`engine_ready`, `last_inference_ms`) at ≤ 1 Hz; never poll `/warmup` as a health check (it takes the inference lock).
- **H2 (step 8).** On failover the `source` changes, so the phone sees entirely new `(source, track_id)` keys. Step 9's A6 spatial refractory is the only thing preventing a double announcement across the flip — do not add a step-8-side suppression, and do not reuse `track_id` values across sources.
- **H3 (step 8).** A rung step does not rebuild trackers (A7), so ids survive it; but `frame_rate` stays at the first rung's value, giving ~±1 s of lost-buffer error after a step. Acceptable; do not "fix" it by rebuilding.
- **H4 (step 9 H5, demo decision).** The cyclist TTL is 500 ms and the cloud path's p95 is 400–600 ms, so the cyclist segment will produce legitimate stale drops on the cloud. Decide **before** the demo whether that segment runs on the local detector. This is §4.4 working; do not loosen the TTL.
- **H5 (step 9 H7, step 4).** `class_map.py` is the canonical producer-side COCO mapping; the phone's step-4 detector must use the same 12-value list and the same COCO rows, or a classified-vs-classified disagreement announces twice. The per-COCO height overrides (A5) should be mirrored on the phone if it ever bands from box height.
- **H6 (step 15, hazard map).** The cloud emits no bearing and no position, and its `gen` counter resets on process restart. Firestore writes must happen bridge-side after bearing computation, and hazard keys must be namespaced by `session_id` — a bare `track_id` is not globally unique across cloud restarts.
- **H7 (step 16, latency bench).** `inference_ms` **includes** JPEG decode (A8). To separate model time from decode, compare `/warmup`'s number (model only) against `/infer`'s. If decode exceeds ~6 ms on the L4, the fix is a module-level `ThreadPoolExecutor(max_workers=DECODE_WORKERS)` around `cv2.imdecode` (it releases the GIL) — a five-line change, deliberately not done now.
- **H8 (demo).** The dynamic engine pays a one-time context reshape on the first frame at each shape. If step 8 steps a rung on stage, that frame is slower. `warmup_loop.sh` warms both shapes; keep it running.
- **H9 (anyone).** Do not re-run `uv lock`. `supervision` is pinned `<0.31`, `ultralytics` already drifted 8.3 → 8.4.163, and `tensorrt` resolves to an unpinned CUDA-13 build.

## 5. Final file list after amendments

`backend/src/cane/cloud/` (line budgets are limits, not targets):
| file | purpose | ≤ |
|---|---|---|
| `__init__.py` | empty | 5 |
| `api_models.py` | `InferenceMeta`, `Detection`, `InferenceResponse` (A2) | 120 |
| `settings.py` | `CloudSettings` (A3) | 70 |
| `class_map.py` | `COCO_TO_CLASS`, `HEIGHT_M_BY_CLASS`, `HEIGHT_M_BY_COCO`, `map_coco`, `height_m` (A5) | 100 |
| `motion.py` | pure geometry + `MotionState` + `update_motion` (A6) | 130 |
| `tracker.py` | `CropTracker`, `TrackerRegistry`, `SessionCrop`, rung fps (A7) — the only supervision import | 170 |
| `service.py` | `InferenceService.infer` (A8) — no fastapi import | 150 |
| `app.py` | routes, `AppState`, lock, limits, errors (A12, A13) | 160 |
| `detector/__init__.py` | empty | 5 |
| `detector/base.py` | `RawDetection`, `FrameDetector` Protocol (A4) | 45 |
| `detector/fake.py` | `ScriptedDetector` — deterministic, per-call boxes | 70 |
| `detector/ultralytics_yolo.py` | `UltralyticsDetector` for `.pt` and `.engine` (A4) | 100 |
| `detector/factory.py` | `build_detector(settings)`, lazy imports | 45 |
| `export_engine.py` | tier 2: GPU-fingerprinted, atomic TRT export (A11) | 90 |
| `smoke.py` | `python -m cane.cloud.smoke` + pure `evaluate` (A14) | 120 |

`backend/docker/`: `Dockerfile`, `entrypoint.sh` (both tier 2, A1 header).
`backend/deploy/`: `create_l4_vm.sh`, `warmup_loop.sh`, `README.md` (~40 lines: build, deploy, smoke, cold-start expectations, `us-east1`, the static-engine escape hatch, the three `build_and_push` commands) — all tier 2.
`backend/pyproject.toml`: `supervision>=0.25,<0.31`; `gpu` extra markered to `sys_platform == 'linux'`; narrow `filterwarnings` only if §A15.5 shows they are needed.
`.gitignore`: `.venv-yolo/`, `*.engine`, `*.engine.json`, `yolo11s.pt`.

`backend/tests/cloud/`:
| file | tests |
|---|---|
| `conftest.py` | `fake_detector`, `app_with_fake`, `six_jpegs(crop_px)` (encoded once, session-scoped), `meta_json` |
| `test_api_models.py` | `test_schema_alias_round_trips_as_schema`, `test_t_capture_ms_outside_contract_window_is_rejected`, `test_yaws_deg_accepts_integers`, `test_yaws_deg_requires_exactly_six`, `test_models_are_frozen_and_forbid_extra`, `test_crop_px_must_be_multiple_of_32`, `test_session_id_pattern_rejects_path_traversal` |
| `test_class_map.py` | `test_readme_rows_bicycle_motorcycle_truck`, `test_unmapped_name_returns_none`, `test_every_object_class_has_a_height`, `test_coco_height_override_beats_class_default`, `test_mapping_is_case_insensitive` |
| `test_motion.py` | `test_focal_px_matches_60_deg_fov`, `test_band_boundaries_person_4_9_5_1_14_9_15_1`, `test_band_is_near_at_exactly_5_and_mid_at_exactly_15`, `test_range_is_invariant_to_crop_px`, `test_closing_6_7_mps_over_three_frames_within_10_percent`, `test_receding_is_negative`, `test_first_sample_returns_none`, `test_dt_zero_or_negative_returns_none_and_keeps_baseline`, `test_dt_over_2000ms_resets_baseline`, `test_class_height_change_resets_baseline`, `test_raw_over_40_mps_is_rejected_not_clamped`, `test_ema_halves_a_step_change`, `test_result_is_within_contract_bounds` |
| `test_tracker.py` | `test_supervision_constructor_accepts_our_kwargs`, `test_track_id_stable_across_30_frames_slow_box`, `test_track_id_stable_across_30_frames_fast_box`, `test_two_crops_have_independent_ids`, `test_det_index_mapping_survives_tracking`, `test_empty_frame_still_ages_lost_tracks`, `test_track_survives_two_seconds_of_misses_and_then_dies`, `test_track_id_encodes_crop_index_and_generation`, `test_generation_increments_so_ids_never_repeat`, `test_registry_evicts_idle_entry_after_5s_of_capture_time`, `test_registry_caps_sessions`, `test_rung_change_does_not_rebuild_the_tracker` |
| `test_app.py` | `test_infer_returns_valid_response`, `test_t_capture_ms_is_echoed_byte_identical`, `test_response_json_mentions_no_metres_and_no_bearing`, `test_detection_model_has_no_forbidden_fields`, `test_request_path_survives_a_raising_time_time`, `test_malformed_meta_returns_422`, `test_missing_crop_returns_422`, `test_crop_size_mismatch_returns_422`, `test_undecodable_jpeg_returns_422`, `test_oversize_crop_returns_413`, `test_api_key_required_when_configured`, `test_healthz_shape`, `test_warmup_warms_both_shapes`, `test_detector_failure_returns_503_without_a_traceback`, `test_concurrent_requests_are_serialized`, `test_non_inference_overhead_p50_under_budget` |
| `test_service_contract.py` | `test_response_converts_to_a_valid_x4_cloud_awareness_event`, `test_converted_event_always_has_range_m_none` |
| `test_no_clock_reads.py` | `test_request_path_modules_contain_no_wall_clock_reads` |
| `test_no_heavy_imports.py` | `test_importing_the_app_does_not_import_torch_or_ultralytics`, `test_full_request_does_not_import_torch` |
| `test_smoke.py` | `test_evaluate_fails_on_slow_median`, `test_evaluate_fails_when_engine_not_ready`, `test_evaluate_ignores_the_first_frame` |
