# Step 22 brief — Depth head in the cloud inference service

Written after implementation, 2026-09-26. Binding spec: `docs/shepherd-steering-plan.md`
§0–§4 and its Step 22. Everything below either quotes a measurement or names the
place the plan had to be interpreted. Step 21's `GapProfile` shape was taken as
given and not redesigned.

## A. What was built

`POST /infer` now also answers with one `GapProfile` per forward crop — C5/C0/C1,
yaw centres −60/0/+60 — alongside the detections it already returned.

| file | what it is |
|---|---|
| `backend/src/cane/cloud/depth/__init__.py` | the package's three rules: no per-frame normalisation, absence never a fake, no clock |
| `backend/src/cane/cloud/depth/constants.py` | plan §3's table, the cloud's rows; the only module allowed to name a path |
| `backend/src/cane/cloud/depth/geometry.py` | the obstacle-band filter and the ground rows, on `dewarp.focal_px` |
| `backend/src/cane/cloud/depth/profile.py` | 16 raw column means + the ground fit, `CropDepthProfile` → `GapProfile` |
| `backend/src/cane/cloud/depth/base.py` | the `DepthEstimator` boundary; zero heavy imports |
| `backend/src/cane/cloud/depth/fake.py` | `ScriptedDepth`, mirroring `detector/fake.py` |
| `backend/src/cane/cloud/depth/head.py` | lifetime + failure policy: one load attempt, per-crop isolation |
| `backend/src/cane/cloud/depth/torch_depth.py` | reuses `benchmarks.depth_model.load_depth`/`predict_depth` verbatim |
| `backend/src/cane/cloud/depth/preprocess.py` | the official transform, engine path only, square crops only |
| `backend/src/cane/cloud/depth/trt_depth.py` | **UNVERIFIED** TensorRT estimator; testable policy, isolated CUDA calls |
| `backend/src/cane/cloud/depth/export_depth_engine.py` | **UNVERIFIED** operator tool: ONNX → engine, fingerprinted cache |
| `backend/src/cane/cloud/depth/factory.py` | the posture table, and TensorRT→torch fallback |
| `backend/src/cane/cloud/settings.py` | `CANE_DEPTH_*` knobs (edited) |
| `backend/src/cane/cloud/api_models.py` | one `model_validator`: at most one profile per `crop_index` (edited) |
| `backend/src/cane/cloud/service.py` | depth after detections, folded into `inference_ms` (edited) |
| `backend/src/cane/cloud/app.py` | `build_state(..., depth=)`, `/warmup` warms depth too (edited) |

Per crop: predict → vertical band filter → 16 column means of **raw** relative
inverse depth → ground-plane fit → `GapProfile`.

### The band filter (plan §2.3)

A point `a` metres above the ground, `d` metres away, lands
`horizon + focal_px·(h − a)/d` pixels down a level crop. The horizon is
`crop_px/2 − 0.5`, matching `dewarp`'s pixel-centre sampling exactly. For
`crop_px = 640`, `h = 1.40`, band `0.20…2.20 m`, reference distance `3.00 m`:

```
focal  = 320 / tan(30°)              = 554.256 px
top    = 319.5 − 554.256·0.80/3.00   = 171.698 → 171
bottom = 319.5 + 554.256·1.20/3.00   = 541.203 → 542   (exclusive)
```

`band_rows = (171, 542)` — 371 of 640 rows, always strictly inside the frame for
every contract-legal `crop_px` (tested for all of 256…1280 step 32), and always
bracketing the horizon.

### The ground anchor

The rows the band filter throws away at the bottom (542…640 at 640 px) are the
floor, and therefore the scale sample: disjoint from the band by construction, so
a wall in the band cannot contaminate the fit and a covered floor cannot move the
columns. Per-row evidence is the row's **median** across columns, because the
floor is exactly what a chair leg covers. Fit `z = scale·(1/d)` through the
origin; `residual = rms(z − scale/d) / (scale·std(1/d))` — a ratio, so it is
thresholdable even though relative depth is not.

## B. Numbers

### Measured here, on this Mac

Real X4 equirect frame (`docs/steps/assets/step-06-live-equirect.jpg`), dewarped
by `cane.bridge.dewarp` to three 640-px crops, Depth Anything V2 Small at
`input_size = 280`, Apple MPS, 8 iterations after two warm-ups:

| quantity | value |
|---|---|
| three forward crops, whole head | **p50 92.2 ms** (min 91.3, max 111.2) |
| per-crop `predict_depth` | **p50 30.2 ms** |
| head's own arithmetic, 3 crops (band + 16 columns + ground fit) | **1.69 ms** (≈0.56 ms/crop) |
| first-call warm-up (graph compilation) | 930 ms — hence `/warmup` |
| raw depth span on real crops | 0.000…5.844 (arbitrary inverse-depth units) |

That is consistent with the pre-step measurement in plan §4.5 (31.8 ms p50 per
crop / ~95 ms for three, `deploy/depth_crop_timing.py`): the head adds under
2 % on top of inference. **The cost is the model, not this step's code.**

The §1.1 guard was re-checked against the real model, not only the fakes: the
same crop offset by a constant 0.5 moved every column by exactly 0.5.

### The L4's 25 ms target (plan §4.5) — unknown, needs the L4

Nothing on this Mac can measure or even build a TensorRT engine, and no number in
`trt_depth.py` or `export_depth_engine.py` is a measurement; both carry the
`# UNVERIFIED as of 2026-09-26: not executed.` header step 7's tier-2 files use,
and a test asserts that header and that neither file claims an L4 number.

What is now bounded: the head's own arithmetic is ~0.6 ms per crop, so essentially
all of the 25 ms budget is available to inference. Reaching 25 ms for three crops
needs ~8.3 ms per crop, i.e. a ~3.6× win over MPS's 30.2 ms. YOLO saw ~15×
(178 ms → 12 ms), so it is plausible — and still unproven. If it misses, the
plan's own escape (C0 alone, 16 columns, 60°, exactly Shepherd's coverage) needs
one constant changed: `DEPTH_CROPS`.

## C. Open questions and where the plan was wrong

### 1. The one-parameter ground fit does not anchor on real frames (measured)

Plan §2.3 prescribes "a one-parameter fit from relative depth to metres". On the
real frame, all three crops were **rejected** (residuals 0.686 / 0.922 / 0.892
against the 0.25 threshold). The reason is measurable: Depth Anything V2 is
affine-invariant in disparity (`z = a/d + b`), and on that frame the offset
dominates the signal over the visible floor strip.

| crop | one-parameter rms | affine (`a/d + b`) rms | fitted `a` | fitted `b` |
|---|---|---|---|---|
| 5 | 0.269 | **0.068** | 3.658 | 2.509 |
| 0 | 0.365 | **0.005** | 0.905 | 3.519 |
| 1 | 0.396 | **0.013** | 1.373 | 3.810 |

The shipped behaviour is the plan's, deliberately: rejecting beats shipping a bad
scale (the one-parameter fit's own scale would have put a 3.49 m ground row at
2.92 m, a 16 % error). But step 23 must not expect `anchored = true` in the field.
**Recommended amendment:** fit two parameters and carry both, `d ≈ a/(z − b)`.
That is still geometric recovery, not the §1.1 trap — the offset comes from the
ground plane, never from the frame's min/max.

Until that lands, plan risk #1's mitigation is the live behaviour: ship gap
*direction* and `blocked`, and hold `proximityFactor` at a constant.

### 2. `GapProfile` cannot carry the scale at all

Even with a perfect fit, `GapProfile` has fields for `ground_fit_residual` and
`anchored` but none for the fitted scale, so **step 23 cannot convert raw columns
into metres from the wire alone** — and §2.3's `proximityFactor` is defined in
metres. `CropDepthProfile.ground_scale` holds the value on the cloud side so that
closing the gap is a contract amendment (step 21's territory), not new maths.

### 3. The plan names no band reference distance

The band is a rectangle in (row, distance) space, so it needs one distance; §2.3
gives the relation and §3 gives no reference. `BAND_REFERENCE_M = D_CLEAR_M`
(3.00 m) was chosen and documented: it is the farthest distance the steering ramp
reacts to, so rows that could only hold floor or ceiling at 3 m carry no steering
evidence. Nearer obstacles span the band and more, so none is lost. Evaluating the
band over the whole `[D_STOP, D_CLEAR]` range instead yields the entire frame,
which is why a single reference is necessary rather than a simplification.

### 4. The plan names no residual threshold

`DEFAULT_MAX_GROUND_RESIDUAL = 0.25`, with the derivation written down: a flat
wall filling the floor rows scores ≈1.0 and is rejected. Note also that
normalising the residual by the *level* rather than by the fit's dynamic range
would have been far too forgiving — a 60° crop only sees the floor from 2.42 m to
3.49 m, so a constant depth already sits within ~10 % of the best line through the
origin, and a wall would have anchored. `CANE_DEPTH_MAX_GROUND_RESIDUAL` tunes it.

### 5. `inference_ms` now includes the depth head

Step 8's health monitor and step 16's bench read `inference_ms` as the whole
server-side cost, so depth is inside it (≈92 ms of it on the laptop). Anyone
comparing a pre-step-22 number to a post-step-22 number is comparing two
different things. The detection half is unchanged and byte-identical with depth
off (asserted; `track_id`'s generation stamp is a process-wide counter and is
normalised in that test).

### 6. Depth readiness is on `/warmup`, not `/healthz`

`/healthz`'s shape is asserted by set equality in step 7's suite and is polled by
step 8's monitor, so it was left exactly as it was. `/warmup` now answers
`{"depth": {"backend": …, "ready": …}}` and a per-shape `depth_ms`. If step 25
wants depth on `/healthz`, that is a deliberate contract-shaped change, not a
drive-by.

### 7. The posture default, and why it is not a plain "on"

`CANE_DEPTH_ENABLED` defaults **true** because the tier-2 deploy files
(`docker/Dockerfile`, `deploy/startup.sh`) were outside step 22's scope, so there
must be no new env var on the L4 to forget. Which backend then runs follows the
detector the container already sets: `fake → off`, `ultralytics → torch`,
`tensorrt → tensorrt` (with the torch fallback). `CANE_DEPTH_BACKEND` overrides.
Consequence worth knowing: a process serving the scripted detector loads no depth
model at all, which is what keeps the cloud suite torch-free.

One caveat this leaves: run the cloud suite inside `.venv-vision`/`.venv-yolo`
with `CANE_DEPTH_BACKEND=torch` exported and `test_no_heavy_imports` would see a
real torch import. In the default posture, in any environment, it does not.

### 8. A clock-guard hole inherited from the benchmark

`benchmarks/depth_model.py` imports `synchronize` from `benchmarks/yolo.py`, which
is a CLI that *does* read a wall clock. That module cannot be guarded, so the AST
test pins the boundary instead: the only symbol crossing it is `synchronize`, a
torch device barrier. `cane.bridge.dewarp` and `cane.benchmarks.depth_model` are
now both clock-guarded by name, extending step 7's test rather than adding a
second mechanism.

### 9. The TensorRT path's preprocessing is written twice

The torch backend calls the official `infer_image`; an engine has no such wrapper,
so `preprocess.py` reimplements the transform. It is exactly equivalent **because
our crops are square and `depth_input_size` is on the ViT patch grid** — both
preconditions are enforced with a raise, not assumed. Step 25 should still
cross-check one real crop through both paths on the L4.

## D. Tests

`cd backend && uv run pytest tests/cloud -q` → **399 passed** (168 before this
step; 231 new). No test imports torch, none is skipped on hardware, and the real
model was exercised once by hand through `.venv-vision`.

| acceptance criterion | where |
|---|---|
| 1. wall left → nearer left; right → mirror | `test_depth_profile.py::test_a_wall_on_the_left_reads_nearer_on_the_left`, `…the_exact_mirror` |
| 2. constant offset changes the columns | `test_depth_profile.py::test_a_constant_depth_offset_changes_the_columns` (+ the real-model check in §B) |
| 3. no clock in the depth path | `test_no_clock_reads.py`, extended: 10 new modules, 2 external, + the `synchronize` boundary |
| 4. failed load → detections, no profiles | `test_depth_head.py::test_a_loader_that_raises_…`, `test_depth_service.py::test_a_broken_depth_model_is_not_a_503` |
| 5. band rows in frame, hand-computed | `test_depth_geometry.py::test_band_rows_match_the_hand_computed_geometry`, `…strictly_inside_the_frame` |
| 6. one profile per `crop_index` | `test_gap_profile_uniqueness.py` (7 cases) |
| 7. detection path unchanged with depth off | `test_depth_service.py::test_the_detection_path_is_byte_identical_with_depth_off` |

Guards worth naming separately: `test_depth_source_guards.py` asserts
`normalize_depth` appears nowhere in `cane.cloud` (§1.1 at source level), that the
two L4-only modules carry the UNVERIFIED header and claim no measured L4 number,
that only `constants.py` names a path, and that `focal_px` is imported from the
bridge rather than re-derived. Removing one module from the clock guard's list was
checked to make the reachability test fail, so the guard has teeth.

## E. Out of scope, as the plan says

Steering (23), the phone (24), depth on the rear crops. Not touched: `contracts/`,
`ios/`, `cane.bridge`, `cane.transport`, the tier-2 deploy files. Nothing was
committed.
