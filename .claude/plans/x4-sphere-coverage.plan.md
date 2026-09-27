# Plan: X4 Sphere Coverage

**Source PRD**: `.claude/prds/x4-sphere-coverage.prd.md`
**Selected Milestone**: 1 — Floor visible offline
**Complexity**: Large (small geometry change, wide contract blast radius: ~20 files, 27 test modules reference the crop count)

## Summary
Add a seventh crop, `C6`, a 120° pinhole view centred on the nadir with image-up = forward, so the
detector input reaches −90° at every bearing while the six proven 60° ring crops (and every step-6
to step-27 consumer of them: bearing math, the 48-column gap ring, the ground fit, the VLM prompt)
stay byte-identical. The wearer's own body is blacked out inside a fixed disc around the nadir.
The crop count moves from 6 to 7 in the shared inference contract, which is the whole cost:
fixtures, the rung bitrate table, the engine batch and the contact sheets follow the one constant.

### Why this shape and not the alternatives
| Option | Reaches | Contract | Steering geometry | Whole sphere later |
|---|---|---|---|---|
| **A. Pitch the six ring crops down ~25°** | −55° to +5° | unchanged | breaks (horizon leaves the crop centre; ground-row fit and band filter in steps 22/27 assume it) and opens ~5° seam gaps at the horizon | no |
| **B. Cubemap (six 90° faces)** | everything | count unchanged, yaw table changes | breaks (three forward 60° crops become one 90° face) and loses 33 % pixel density on far hazards | yes |
| **C. Seventh nadir crop (chosen)** | −90° to +30° | count 6 → 7 | untouched | yes: an eighth zenith crop is the same code with pitch +90 |

Option C is the only one that meets the PRD's "no range regression" and "no steering regression"
metrics without re-measuring the depth head. If the user rules the six-crop API frozen, fall back
to A and accept the steering rework; that is a different plan.

## Patterns to Mirror
| Category | Source | Pattern |
|---|---|---|
| Naming | `backend/src/cane/bridge/gap_ring.py:45` | Module-level `UPPER_CASE` constants with a `#:` comment stating the design source |
| Naming | `backend/tests/bridge/test_dewarp.py:66` | Test names are full sentences: `test_a_stripe_at_a_yaw_centre_lands_in_the_middle_of_its_crop` |
| Errors | `backend/src/cane/bridge/dewarp.py:104` | Input validation raises `ValueError` with the fix in the message ("is the X4 in its 2880x1440 panorama mode?") |
| Errors | `backend/src/cane/bridge/events.py:80` | A detection the bridge cannot place is dropped with `logger.warning`, never raised into the loop |
| Logging | `backend/src/cane/bridge/snapshot.py:22` | `logging.getLogger("cane.bridge.<module>")`, `%`-style args, warnings for dropped data |
| Data access | `backend/src/cane/bridge/dewarp.py:96` | Remap tables built once by an `lru_cache`d builder keyed on `(W, H, crop_px)`; per-frame cost is `cv2.remap` only |
| Tests | `backend/tests/bridge/test_dewarp.py:31` | Synthetic equirect with a painted stripe, intensity-weighted centroid, tolerance stated in crop pixels |
| Tests | `backend/tests/bridge/test_events.py:64` | `pytest.mark.parametrize` over `(crop_index, cx, expected_bearing)` |
| Tests | `backend/tests/bridge/conftest.py:62` | Fixtures derive the JPEG count from `CROP_COUNT`, not a literal |
| Contract | `backend/src/cane/cloud/api_models.py:31` | `CROP_COUNT` is the single source for field names, index bounds and `yaws_deg` length |

## Files to Change
| File | Action | Why |
|---|---|---|
| `backend/src/cane/cloud/api_models.py` | UPDATE | `CROP_COUNT = 7`; docstring for `crop_field_name` (`crop_0` .. `crop_6`) |
| `backend/src/cane/bridge/dewarp.py` | UPDATE | `NADIR_CROP_INDEX`, `NADIR_FOV_DEG`, `NADIR_BODY_MASK_DEG`, `_nadir_lut`, mask, 7-LUT builder; `CROP_YAWS_DEG` (six ring yaws) unchanged; assert LUT count == contract `CROP_COUNT` |
| `backend/src/cane/bridge/events.py` | UPDATE | Nadir branch: bearing `atan2(x, −y) + delta`, `range_band` from depression angle and `MOUNT_HEIGHT_M`, `closing_mps = None` |
| `backend/src/cane/bridge/rungs.py` | UPDATE | `crops` per rung = `CROP_COUNT`; §3.2 bitrate numbers scale by 7/6 |
| `backend/src/cane/cloud/export_engine.py` | UPDATE | `STATIC_BATCH = CROP_COUNT`, `EXPORT_BATCH` default `2 * CROP_COUNT`; fingerprint then rejects the old batch-6 engine (deliberate, see risks) |
| `backend/src/cane/bridge/snapshot.py` | UPDATE | Contact sheet 4×2 with one blank cell; label `C6 nadir fov 120` |
| `backend/src/cane/benchmarks/crop_layout.py` | UPDATE | Grid keyed by crop index, not yaw (the nadir's yaw 0 would collide with Front); label `Down` |
| `backend/src/cane/bridge/backends.py`, `transport_http.py` | UPDATE | Docstrings say "six"; behaviour already derives from `CROP_COUNT` |
| `backend/tests/bridge/conftest.py`, `backend/tests/cloud/conftest.py` | UPDATE | `YAWS` gains the nadir entry `0.0`; fixture name `six_jpegs` → `crop_jpegs` |
| `backend/tests/bridge/test_dewarp.py` | UPDATE | Nadir stripe tests, mask test, coverage test, 7-LUT shape test |
| `backend/tests/bridge/test_events.py` | UPDATE | Nadir bearing/band/closing cases; drop-if-out-of-range case now uses index 7 |
| `backend/tests/bridge/test_rungs_config.py`, `test_snapshot.py`, `test_api_models.py`, `backend/tests/cloud/test_api_models.py`, `backend/tests/benchmarks/test_crop_layout.py` | UPDATE | Literal sixes and bitrate expectations |
| `docs/device-responsibilities.md` | UPDATE | Crop table gains the `C6` row |
| `docs/camera-sync-design.md` | UPDATE | §1.3 note on vertical coverage; decision-log row dated 2026-09-26 |
| `docs/steps/assets/x4-sphere-coverage-crops.jpg` | CREATE | Offline proof: the step-06 equirect's seven crops, table contents visible in `C6` |

## Tasks
### Task 1: Move the contract to seven crops and let the fixtures follow
- **Action**: Set `CROP_COUNT = 7` in `api_models.py`. In both conftests append `0.0` to `YAWS` (the nadir crop is oriented with forward at image-up, so yaw 0 is its honest entry) and rename `six_jpegs` to `crop_jpegs`. Fix every test that spells out six (`grep -rnE "== 6|\* 6|range\(6\)|crop_5" backend/tests`). Do not touch `CROP_YAWS_DEG`.
- **Mirror**: `backend/tests/bridge/conftest.py:62` (count from the constant).
- **Validate**: `cd backend && uv run pytest tests/bridge/test_api_models.py tests/cloud/test_api_models.py -q` passes; `uv run pytest -q` shows only dewarp/events/rungs/layout failures, which the next tasks own.

### Task 2: Nadir remap table and body mask (tests first)
- **Action**: Write the failing tests: a horizontal stripe painted at latitude −45° appears in crop 6 as a ring of radius `focal_nadir · tan(45°)` ± 1.5 px and in no ring crop; a vertical stripe at bearing 0 appears in crop 6 as a radial line from the centre to the top edge, at bearing +90 to the right edge; a stripe at latitude −80° (inside the mask) appears nowhere and the disc is pure black; `build_remap_luts` returns `CROP_COUNT` tables; a coverage test walks every equirect column and asserts the union of the seven crops' sampled latitudes reaches −55° (PRD metric 1) and in fact −90° outside the mask. Then implement `_nadir_lut(width, height, crop_px)`: same ray model as `_crop_lut` with `world_x = x`, `world_z = −y`, `world_y = focal_nadir`, `focal_nadir = (crop_px/2) / tan(NADIR_FOV_DEG/2)`; after `cv2.remap`, `cv2.circle` a filled black disc of radius `focal_nadir · tan(NADIR_BODY_MASK_DEG)`. Constants: `NADIR_CROP_INDEX = 6`, `NADIR_FOV_DEG = 120.0` (covers −30° to −90°, meeting the ring at its lower edge), `NADIR_BODY_MASK_DEG = 35.0` (provisional; milestone 2 measures it on the wearer). `CropSet.yaws_deg` becomes the seven-entry tuple; `CROP_YAWS_DEG` stays the six ring yaws so `test_yaw_centres_are_the_device_responsibilities_table` and the gap ring are untouched.
- **Mirror**: `backend/src/cane/bridge/dewarp.py:70` (`_crop_lut` ray model) and `backend/tests/bridge/test_dewarp.py:31` (stripe helpers).
- **Validate**: `cd backend && uv run pytest tests/bridge/test_dewarp.py -q`.

### Task 3: Bearing, band and closing speed for a nadir detection (tests first)
- **Action**: Parametrized cases: `(6, cx=0.5, cy=0.25)` → bearing 0; `(6, 0.75, 0.5)` → +90; `(6, 0.5, 0.75)` → 180; `(6, 0.25, 0.5)` → −90; `delta_deg` still rotates it; `range_band` is `near` for any nadir detection (depression ≥ 30° at `MOUNT_HEIGHT_M` 1.40 m puts it inside 2.42 m, so `band_for_range` from `cane.cloud.motion` returns `NEAR`; keep the formula, not the literal); `closing_mps` is `None` because the cloud's height-based range is meaningless looking down; a detection with `crop_index == 7` is still dropped and logged. Implement in `events.py` as a branch on `NADIR_CROP_INDEX` before the linear ring formula: `x = cx − 0.5`, `y = cy − 0.5`, bearing `= degrees(atan2(x, −y)) + delta`, off-nadir `= atan(2 · hypot(x, y) · tan(NADIR_FOV_DEG/2))`, floor distance `= MOUNT_HEIGHT_M · tan(off_nadir)`.
- **Mirror**: `backend/src/cane/bridge/events.py:57` (bearing function shape) and `:80` (drop-and-log).
- **Validate**: `cd backend && uv run pytest tests/bridge/test_events.py -q`.

### Task 4: Rungs and engine batch follow the crop count
- **Action**: `Rung.crops` values come from `CROP_COUNT`; update `test_rungs_config.py` expectations (rung 1 ≈ 7.6 Mbps, rung 2 ≈ 5.0 Mbps; recompute with `bitrate_estimate_mbps`, do not hand-type). In `export_engine.py` derive `STATIC_BATCH` and the `EXPORT_BATCH` default from `CROP_COUNT` and update the two comments. The fingerprint check in `app.py` will now refuse the deployed batch-6 engine, which is the intended guard against a silent mismatch.
- **Mirror**: `backend/src/cane/bridge/rungs.py:44` (bitrate estimate reproduces the published table).
- **Validate**: `cd backend && uv run pytest tests/bridge/test_rungs_config.py tests/cloud -q`.

### Task 5: Contact sheets show seven crops
- **Action**: `snapshot.build_contact_sheet` tiles 4×2 with a blank last cell and labels crop 6 `C6 nadir fov 120`. `benchmarks/crop_layout.py` keys its grid by crop index `(5, 0, 1, 4, 3, 2, 6)` with labels indexed the same way (`Down` for 6); `tile()` keeps raising on a count mismatch.
- **Mirror**: `backend/src/cane/bridge/snapshot.py:46` (labelled tiling) and `backend/src/cane/benchmarks/crop_layout.py:103` (count check raises).
- **Validate**: `cd backend && uv run pytest tests/bridge/test_snapshot.py tests/benchmarks/test_crop_layout.py -q`.

### Task 6: Full suite, offline proof, and the spec
- **Action**: Run the whole backend suite (baseline 1096 passed / 2 skipped). Produce the proof artefact: `PYTHONPATH=src uv run python -m cane.bridge.snapshot --source file:../docs/steps/assets/step-06-live-equirect.jpg --out ../docs/steps/assets` then rename the sheet to `x4-sphere-coverage-crops.jpg`; the table top and bottle bases must be visible in `C6`. Optional manual check in `.venv-yolo`: run the local detector on the seven crops and confirm raw detections with `crop_index == 6` (COCO `bottle` is not in the closed enum, so this is a raw-detector check, not an event check). Update `docs/device-responsibilities.md` crop table (`C6 | nadir, 120° | −30° … −90° all round, body masked`), add a §1.3 paragraph and a decision-log row to `docs/camera-sync-design.md`, and refresh the dewarp docstring table.
- **Mirror**: `docs/steps/assets/step-06-live-crops.jpg` (a snapshot artefact is the visual proof of geometry).
- **Validate**: `cd backend && uv run pytest -q` all green; the new JPEG exists and shows the table in `C6`.

## Validation
```bash
cd backend && uv run pytest -q                                   # full backend suite; baseline 1096 passed / 2 skipped
cd backend && uv run pytest tests/bridge/test_dewarp.py tests/bridge/test_events.py -q
cd backend && PYTHONPATH=src uv run python - <<'PY'              # PRD metric 1, the same probe used on 2026-09-26
from cane.bridge.dewarp import build_remap_luts
luts = build_remap_luts(2880, 1440, 640)
print(len(luts), "crops;", "nadir min latitude", (0.5 - luts[6].map_y.max() / 1440) * 180)
PY
cd backend && PYTHONPATH=src uv run python -m cane.bridge.snapshot --source file:../docs/steps/assets/step-06-live-equirect.jpg --out /tmp/x4-sphere
cd ios/CaneKit && swift test                                     # phone contract untouched; proves nothing else moved (433 / 0)
```
No linter is configured in `backend/pyproject.toml`; match the surrounding style by hand.

## Risks
| Risk | Likelihood | Mitigation |
|---|---|---|
| The deployed L4 engine is static batch-6; the new fingerprint rejects it and the cloud will not start until re-exported (~6 min build, milestone 2) | Certain | Deliberate: a silent mismatch would 422 every frame and hide behind the local fallback. Milestone 2 re-exports on the VM; until then `./startcane` runs the local detector, which is batch-agnostic |
| YOLO on a 120° down-looking view is out of its training distribution (radial stretch at the edges) | Medium | The band that matters (−30° to −55°) sits at 35°–60° off-axis where sampling is 5–13 px/deg and objects are within 2.4 m, so large; verify with the raw-detector check in Task 6 and live in milestone 2 |
| Own-body false positives if `NADIR_BODY_MASK_DEG` is too small, or a real object hidden if too large | High until measured | Constant in one place; milestone 2 measures the wearer on the actual mount. The white cane tip near 1 m sits at the disc edge, so watch for `unknown_obstacle` from it |
| +17 % upload (rung 1 6.5 → 7.6 Mbps) trips the health monitor at the venue | Low | The black disc compresses to almost nothing; rung stepping already handles it; measure in milestone 2 |
| Duplicate events at the −30° boundary where ring and nadir crops meet | Medium | Already the seam behaviour at ±90° yaw; the phone dedups by bearing and TTL. Do not widen the nadir FOV to overlap until the live test shows a gap |
| 27 test modules reference six crops; a missed literal leaves a red suite | Medium | Task 1 greps them up front; the fixtures derive from `CROP_COUNT` so most follow automatically |
| The user rules the six-crop API frozen | Unknown (PRD open question 1) | Then this plan is void and option A applies, with the steering rework it implies; confirm before Task 1 |

## Acceptance
- [x] All tasks complete (2026-09-26, worktree `~/Desktop/shellhacks-x4`, branch `feat/x4-sphere-coverage`)
- [x] Validation passes: backend 1391 passed / 3 skipped; latitude probe prints 7 crops, ring −30…+30, nadir −55…−22 outside the mask (deepest unmasked −55°, the PRD bar); `ios/` untouched so CaneKit is unchanged by construction
- [x] Patterns mirrored, not reinvented
- [x] `docs/steps/assets/x4-sphere-coverage-crops.jpg` shows the table contents in `C6`
- [x] PRD milestone 1 row marked complete with this plan linked

## Found while executing
- The cloud app (`app.py`) and the dev stub server declare one FastAPI upload parameter per crop, so the crop count is spelled out there too; both now carry `crop_6` with an assertion against `CROP_COUNT`.
- The cloud's own debug contact sheet (`debug_dump.py`) tiled 3×2; it now tiles 4×2 with a black last cell like the bridge snapshot.
- Four test modules kept a private six-entry `YAWS`; all now end with the nadir's `0.0`.
- The scene VLM keeps receiving the six ring crops only (`scene/service.py`), because `View.centre_deg`/`fov_deg` cannot express C6's radial bearing; milestone 3 owns that.
- Milestone 2 must re-export the L4 engine (`STATIC_BATCH` is now 7) before the cloud path works live; until then the bridge's local detector carries the nadir crop.
