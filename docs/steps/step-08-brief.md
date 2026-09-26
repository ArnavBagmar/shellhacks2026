# Task 8 brief — Health monitor, rung stepping, local fallback

Extracted from docs/orchestration-runbook.md (Step 8), docs/camera-sync-design.md §3.2–§3.4, §4.4, docs/build-plan.md F8, docs/steps/step-07-design.md hazards H1–H3, docs/steps/step-09-design.md A14 (the phone's health signal), plus controller rulings. Exact values are binding. Depends on steps 6 (bridge) and 7 (cloud service) being on the branch — read their code, not just their briefs.

## A. Runbook step text (verbatim)

**Intent:** Survive a saturated venue uplink without anyone touching a keyboard.
**Chain rationale:** `silent-failure-hunter` is the right specialist here — the whole step is about failures that produce no error, only quietly late warnings.

```bash
/everything-claude-code:orchestrate custom "ecc:tdd-guide,ecc:silent-failure-hunter,ecc:python-reviewer" "[Plan: docs/camera-sync-design.md#step-8] Implement §3.4 automatic fallback behind one Detector interface. Step down a rung at p95 RTT over 500 ms across the last 10 frames; flip to local CoreML on 3 consecutive timeouts, or rung 4 still failing, or the feedback manager's stale-drop rate crossing threshold; step back up one rung after 30 s healthy. Silent and logged, never a keystroke. Four rungs: 6×640² q70 at 3 fps, the same at 2 fps, 6×512² q60 at 3 fps, the same at 2 fps. Acceptance: induced timeouts flip to local within 3 frames; recovery steps up at most once per 30 s; user-visible output is unchanged across the switch"
```

## B. Spec (verbatim)

### camera-sync-design.md §3.4 Automatic fallback
Not a keystroke — a hall with a thousand people on 5G is a genuinely saturated uplink, and a manual switch will not get flipped in time on stage.
```python
# Step DOWN a rung when:  p95 round-trip > 500 ms over last 10 frames
# Flip to LOCAL when:     3 consecutive timeouts
#                     OR  already on rung 4 and still failing
#                     OR  stale-drop rate crosses threshold (§4.4)
# Recover:                step up one rung after 30 s healthy
# User-visible:           nothing. Silent, logged.
```

### camera-sync-design.md §3.2 rungs
| rung | crops | size | q | fps | uplink |
|---|---|---|---|---|---|
| 1 | 6 | 640×640 | 70 | 3 | ≈6.5 Mbps |
| 2 | 6 | 640×640 | 70 | 2 | ≈4.3 Mbps |
| 3 | 6 | 512×512 | 60 | 3 | ≈2.3 Mbps |
| 4 | 6 | 512×512 | 60 | 2 | ≈1.5 Mbps |

### camera-sync-design.md §4.4 (the trigger)
Then reuse the signal: **count the drops.** A rising stale-drop rate is the cleanest trigger for the §3.4 fallback — one mechanism, two jobs, no separate health probe.

### build-plan.md §5 (placement)
| **M2 Pro laptop** | UVC grab · 6-crop remap LUT · JPEG · sender · health monitor · CoreML fallback | ~8–12 ms + I/O | Degrades to fallback detector |
| **Cloud L4** | TensorRT YOLO · ByteTrack · closing speed | … | Health monitor flips to local, silently |

## C. Inherited hazards (verbatim, binding)
- **step-07-design H1.** `service.py`, `tracker.py`, `motion.py` and `class_map.py` are deliberately FastAPI-free and torch-free. The local fallback must be a new `FrameDetector` plus `InferenceService` **in-process on the laptop**, not a reimplementation — otherwise `x4_local` and `x4_cloud` will disagree on bands and closing speed at exactly the moment the health monitor flips. Poll `GET /healthz` (`engine_ready`, `last_inference_ms`) at ≤ 1 Hz; never poll `/warmup` as a health check (it takes the inference lock).
- **step-07-design H2.** On failover the `source` changes, so the phone sees entirely new `(source, track_id)` keys. Step 9's A6 spatial refractory is the only thing preventing a double announcement across the flip — do not add a step-8-side suppression, and do not reuse `track_id` values across sources.
- **step-07-design H3.** A rung step does not rebuild trackers, so ids survive it; `frame_rate` stays at the first rung's value, giving ~±1 s of lost-buffer error after a step. Acceptable; do not "fix" it by rebuilding.
- **step-09-design A14 (the phone side of the signal).** The rate is a ring of 10 one-second buckets `{received, staleDropped}`: `staleDropRate(now) -> Double` (0 when received == 0) and `isStaleDropRateUnhealthy` = rate ≥ `STALE_DROP_RATE_THRESHOLD (0.2)` over at least 2 complete buckets.

## D. What earlier steps built (use, do not re-implement)
- Step 6 (`cane.bridge`): `rungs.py` (RUNG_1..RUNG_4, `RUNGS`), `sender.py` (`OneInFlightSender`, `SendOutcome`, `SenderStats` with frame-age and RTT percentiles), `events.py` (response → `AwarenessEvent`), `pipeline.py` (tick loop paced by rung fps), `config.py` (`BridgeConfig` incl. `request_timeout_s`), `dev_stub_server.py`. Read the actual names.
- Step 7 (`cane.cloud`): `api_models.py`, `service.InferenceService` (sync, FastAPI-free), `tracker.TrackerRegistry`, `detector/base.FrameDetector`, `detector/fake`, `detector/factory.build_detector`, `detector/ultralytics_yolo.UltralyticsDetector` (loads `.pt`, `.engine` and — via ultralytics — `.mlpackage`), `settings.CloudSettings`, `GET /healthz` shape `{detector, engine_ready, gpu, weights, sessions, last_inference_ms}`.
- Step 3 (`cane.transport.server.EventLinkServer`): `_handle_inbound_frame` logs and ignores unknown envelope types today; `contracts/transport_envelope.schema.json` reserves `health` (phone → laptop).
- Contract: `Source` already has `x4_local`; `x4_local` events obey the same rule as `x4_cloud` (`range_m` null).

## E. Controller rulings — binding

### E1. One interface, two backends, one sender
- `cane/bridge/backends.py`: `class InferenceBackend(Protocol)`: `name: str`; `async def infer(self, meta: InferenceMeta, jpegs: tuple[bytes, ...]) -> InferenceResponse` raising `BackendTimeout` / `BackendError(status)`; `source: Source` (`x4_cloud` | `x4_local`).
- `CloudBackend`: wraps the step-6 HTTP transport (httpx, multipart) with `request_timeout_s` (default **1.0 s**; a request slower than that is a *timeout outcome*, not a slow RTT).
- `LocalBackend`: holds an in-process `InferenceService` (step 7) built from `TrackerRegistry` + a `FrameDetector` from `build_detector(CloudSettings(detector=…))`; decodes the same JPEG bytes the cloud would receive (so both paths see identical pixels) and runs `service.infer` in `asyncio.to_thread`. Local `session_id` is `f"{session_id}-local"` so cloud and local trackers never share state.
- `SwitchingBackend`: delegates each call to the backend the `HealthMonitor` currently selects; the step-6 `OneInFlightSender` keeps its one-in-flight rule unchanged and calls the switching backend — local inference also takes time and must never queue.
- Local detector choice (closes "CoreML fallback"): `CANE_LOCAL_DETECTOR` = `fake` (tests, default in `.venv`) | `ultralytics` (default when torch is importable; weights `CANE_LOCAL_WEIGHTS`, default `yolo11s.mlpackage` if it exists, else `yolo11s.pt`, device `mps`). The CoreML export is one documented command (`yolo export model=yolo11s.pt format=coreml imgsz=640 half=True nms=False`, run once in `.venv-yolo`); `.pt` on MPS is an acceptable fallback of the fallback. The bridge process runs in `.venv-yolo` on the demo laptop; tests never import torch (reuse step 7's `sys.modules` assertion for the bridge).

### E2. `HealthMonitor` (`cane/bridge/health.py`) — pure, immutable, clock injected
State (`frozen dataclass HealthState`): `mode: Mode` (`Mode.cloud(rung)` | `Mode.local`), `rtt_window: tuple[float, ...]` (≤ 10), `consecutive_timeouts: int`, `healthy_since_ms: int | None`, `last_step_ms: int | None`, `last_phone_health: PhoneHealth | None`, `transitions: tuple[Transition, ...]` (bounded 32).
Inputs: `observe(outcome: Outcome, now_ms) -> HealthState` where `Outcome` = `Ok(rtt_ms)` | `Timeout` | `Error(status)`; `observe_phone_health(h: PhoneHealth, now_ms)`; `observe_probe(ok: bool, rtt_ms | None, now_ms)` (only while local).
Constants: `RTT_WINDOW = 10`, `STEP_DOWN_P95_MS = 500`, `TIMEOUTS_TO_LOCAL = 3`, `STALE_DROP_RATE_THRESHOLD = 0.2` (equals the phone's), `STALE_MIN_BUCKETS = 2`, `RECOVER_HEALTHY_S = 30`, `PROBE_INTERVAL_S = 1.0`, `MIN_RUNG = 4`, `MAX_RUNG = 1`.
Rules (each one a test):
1. Step DOWN one rung when the window holds 10 samples and p95 > 500 ms (p95 = the 2nd-largest of 10, nearest-rank). After a step the window is **cleared**, so the same samples cannot step twice; at most one step per 10 frames follows from that.
2. Flip to LOCAL on the 3rd consecutive `Timeout` (an `Ok` resets the count; an `Error` counts as a timeout for this rule — a 5xx is also "no answer in time").
3. Flip to LOCAL when already on rung 4 and rule 1 would fire again ("still failing").
4. Flip to LOCAL when the phone reports `stale_drop_rate ≥ 0.2` with `received ≥ 1` over ≥ 2 complete buckets **and the report is fresh** (`now − t_phone_ms ≤ 3000` after clock conversion; stale reports are ignored — a dead signal must not drive a switch).
5. Recovery: while LOCAL, probe the cloud `GET /healthz` every 1 s (async, outside the one-in-flight sender, with the same 1.0 s timeout). Healthy = 200 with `engine_ready` (or `detector != "tensorrt"`) and probe RTT ≤ 500 ms. After **30 s of continuous** healthy probes → `Mode.cloud(4)`, `healthy_since` reset. Any unhealthy probe resets `healthy_since`. While CLOUD on rung r > 1 (i.e. below the top), after 30 s with no timeout and p95 ≤ 500 → step UP one rung; `last_step_ms` enforces **at most one upward step per 30 s** (acceptance). Rung 1 is the top.
6. Phone stale-drop reports do not block recovery by themselves; they only trigger rule 4. Recovery from a rule-4 flip additionally requires the latest phone report (if any) to be below threshold.
7. Every transition appends `Transition(from, to, reason, at_ms)` and emits **exactly one** `logging.INFO` line `health: cloud(1) -> cloud(2) reason=p95_rtt_ms=612`; nothing else is user-visible. Counters (`steps_down, steps_up, flips_to_local, recoveries`) are added to the pipeline's 5 s stats line.

### E3. Rungs are dynamic
`pipeline.py` reads the current rung from the monitor **every tick** (period, crop_px, JPEG q); the step-6 dewarp LUT cache already keys on crop_px; `InferenceMeta.rung` and `crop_px` reflect the rung actually used for that frame. A rung change is logged once (E2.7) and never rebuilds trackers (H3).

### E4. The `health` message (phone → laptop)
Additive `$defs.health` in `contracts/transport_envelope.schema.json` (this is the transport envelope, not the frozen event contract; update its `oneOf` and `contracts/README.md` "Transport envelope"): `{"type": "health", "t_phone_ms": int, "window_ms": int, "received": int, "stale_dropped": int, "stale_drop_rate": number 0..1, "complete_buckets": int}`. Python: `cane/transport/messages.py` gains `PhoneHealth` (frozen pydantic, `extra="forbid"`) and `EventLinkServer` gains `on_health: Callable[[PhoneHealth], None] | None` invoked from `_handle_inbound_frame`; malformed health frames are logged and ignored (never disconnect). The Swift sender of this message is **not** in scope here (ledger note for the step-9/10 integration); the monitor works without it via rules 1–3, 5.

### E5. Tests (`backend/tests/bridge/test_health.py`, `test_backends.py`, `test_fallback_pipeline.py`, `tests/transport/test_health_message.py`; TDD, no hardware, no torch, `-W error` clean)
- `test_health.py`: one test per E2 rule; p95 nearest-rank on 10 samples; window clears after a step; `Ok` resets timeouts; `Error` counts as timeout; rule 4 ignores stale phone reports and reports with < 2 buckets; recovery needs 30 s continuous, one unhealthy probe resets; upward steps ≥ 30 s apart across a 120 s simulated recovery (expect exactly cloud(4)→(3)→(2)→(1) at ≥ 30 s spacing); transitions bounded; log line per transition (caplog) and none otherwise.
- `test_backends.py`: `LocalBackend` with the step-7 `ScriptedDetector` returns an `InferenceResponse` valid under `api_models`, echoing `t_capture_ms`; `CloudBackend` maps httpx timeout → `BackendTimeout`, 5xx → `BackendError`; `SwitchingBackend` routes by mode; **output-unchanged test**: the same six JPEGs + meta through (a) the step-7 ASGI app with a scripted detector and (b) `LocalBackend` with an identical scripted detector produce events (via step-6 `events.py`) equal in every field except `source` and `track_id` (acceptance "user-visible output is unchanged across the switch").
- `test_fallback_pipeline.py`: the step-6 pipeline with `SyntheticSource`, a fake cloud backend scripted to time out from frame 5, a fake `EventLinkServer`/phone client, and an instant fake clock: events arrive as `x4_cloud` for frames 1–4, the monitor is LOCAL by frame 7 at the latest (acceptance "within 3 frames"), events continue as `x4_local` with no gap longer than one tick; when the fake `/healthz` probe turns healthy, recovery to cloud(4) happens after 30 s of fake time and not before; no exception escapes the loop at any point.
- `test_health_message.py`: valid health frame → `on_health` called with the parsed model; malformed → ignored, connection stays open; schema file validates the example.

### E6. Out of scope
The cloud service itself (7), Firestore (15), the benchmark (16), the Swift sender of `health` (step 9/10 integration), any UI or keystroke.

## F. Global constraints (verbatim)
- "Silent and logged, never a keystroke."
- "Acceptance: induced timeouts flip to local within 3 frames; recovery steps up at most once per 30 s; user-visible output is unchanged across the switch"
- Commit format `<type>: <description>`; no attribution trailers. Immutable state; small files; explicit error handling; constants not magic numbers; tests never import torch.
