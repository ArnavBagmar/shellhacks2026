# Step 27 brief — Recover metric scale with a two-parameter affine fit

Written after implementation, 2026-09-26. Binding spec: `docs/shepherd-steering-plan.md`
§2.5 (which supersedes §2.3), §3.1/§3.2/§3.3 (controller rulings) and its Step 27.
TDD throughout: tests first, RED confirmed, then implementation.

`proximityFactor` is now a **measurement** on the anchored path, and the anchored path
is reached on a real X4 frame — all three forward crops, not the two acceptance 1 asked
for. The scale-free path is unchanged to the last decimal and is still the fallback.

## A. What was built

| file | what changed |
|---|---|
| `backend/src/cane/cloud/depth/profile.py` | `fit_ground` is now a least-squares **affine** fit `z = a·(1/d) + b`; `GroundFit` carries `scale`, `offset`, `rms` and the residual; `CropDepthProfile` carries and ships the pair, and refuses to claim `anchored` without it |
| `backend/src/cane/cloud/depth/constants.py` | `RESIDUAL_REFERENCE_M`; `DEFAULT_MAX_GROUND_RESIDUAL` 0.25 → **0.15**, with its derivation written down |
| `backend/src/cane/cloud/api_models.py` | `GapProfile.ground_scale` / `.ground_offset` (optional, both-or-neither), `has_affine_scale`, `GroundScale`/`GroundOffset` wire types |
| `backend/src/cane/bridge/gap_anchor.py` | `CropScale` (one pair **per crop**); `DepthAnchor` gains `offset` + `crop_scales`; `distance_for_depth` gains the offset term and a third outcome (the sky); `AnchorVerdict.NO_SCALE`; `to_inverse_metres` / `inverse_metre_anchor` |
| `backend/src/cane/bridge/steering.py` | `free_distance_m`, `weighted_bearing_deg`, `RunEvidence`; `PassableRun` gains `direction_bearing_deg` and `score`; `choose_run` selects by free distance; `column_distances` converts per crop |
| `backend/src/cane/bridge/steer_frame.py` | `_ring_for`: on an accepted anchor the ring is assembled from per-crop-converted columns |
| `backend/src/cane/bridge/config.py` | `DELTA_DEG_WARN = 15.0` and the blind-wedge warning (step 27 D) |
| `backend/deploy/affine_ground_fit_check.py` | **new**: acceptance 1 against the real model, outside the torch-free suite |
| `backend/tests/cloud/test_affine_ground_fit.py` | **new**, 29 tests: the fit, the §1.1 distinction, the threshold's derivation |
| `backend/tests/{cloud,bridge}/…` | 8 new contract tests, 8 anchor, 10 steering, 8 steer-frame, 3 config; `conftest`/`depth_synthetic` helpers |

**Not touched:** `contracts/` (see §F1), `ios/`, `cane.scene`, `cane.bridge.__main__`,
the tier-2 deploy files. Nothing committed.

## B. The numbers — the proof that this worked

### B1. The real frame, through the shipped code

`docs/steps/assets/step-06-live-equirect.jpg`, dewarped by `cane.bridge.dewarp` to three
640-px crops, Depth Anything V2 Small at `input_size = 280` on Apple MPS, weights
sha256 `715fade13be8f229…`, revision `a561b849ebae`. Produced by
`deploy/affine_ground_fit_check.py`, which calls the production `fit_ground`:

| crop | fitted `a` | fitted `b` | rms | residual | verdict |
|---|---|---|---|---|---|
| 5 (−60°) | 3.658 | 2.509 | **0.0679** | 0.0557 | **anchored** |
| 0 (0°) | 0.905 | 3.519 | **0.0049** | 0.0164 | **anchored** |
| 1 (+60°) | 1.373 | 3.810 | **0.0133** | 0.0291 | **anchored** |

The rms column reproduces §2.5's measured **0.068 / 0.005 / 0.013** exactly, and `a`/`b`
reproduce its 3.7/0.9/1.4 and 2.5/3.5/3.8. **3 of 3 anchored**; acceptance 1 asked for 2.
`ACCEPTANCE 1: PASS`.

The same run under the one-parameter fit scored 0.686 / 0.922 / 0.892 against its 0.25
ceiling — all rejected, reproducing step 22 — so the step's premise is confirmed on the
same data by the same script, not inherited on trust.

### B2. The new residual threshold, and its derivation

`ground_fit_residual` is **redefined**: it was `rms / (a·std(1/d))`, an unarguable ratio in
no unit anyone can reason about; it is now the **fractional distance error the fit's rms
misfit implies at `D_CLEAR_M`**, i.e. `rms · D_CLEAR_M / a`. The identity behind it is
`d = a/(z − b)` ⇒ `δd/d = −(d/a)·δz`. Still dimensionless, still thresholdable, and now a
claim about metres. **`DEFAULT_MAX_GROUND_RESIDUAL = 0.15`**, on three legs:

1. **The bar the plan set itself.** Step 22 refused its one-parameter scale because it
   "would have placed a row whose true distance is 3.49 m at 2.92 m, a 16 % error". 0.15 is
   just inside that: a fit whose misfit implies a worse-than-16 %-class error at the far end
   of the ramp is exactly the fit step 22 was right to refuse.
2. **Margin.** The real crops score 0.056 / 0.016 / 0.029, so 0.15 is 2.7× the worst of
   them. It is not fitted to one frame's last decimal.
3. **Why not 0.25.** `std(1/d)` over the floor rows is 0.0365 /m (a constant of the crop
   geometry — `1/d = (row−horizon)/(focal·h)` and both scale with `crop_px`, so it is the
   same for every rung). So 0.25 in the old units is **0.027** in the new ones: an implicit
   demand for 2.7 % distance accuracy at 3 m, which no real crop meets *even with the right
   model*. Re-using it would have anchored one crop of three and failed acceptance 1.

What the threshold does **not** do is reject a wall, and this is the important half: two
free parameters fit a flat strip *perfectly* (`a = 0`, `b = const`, rms 0), so a residual
test alone would have anchored a wall. That is caught structurally, by requiring `a > 0`.
Measured: an exactly flat strip gives `a = 0.000`; walls tilted 0.02/0.05/0.10/0.30 give
`a` = −0.074/−0.186/−0.372/−1.123, all refused outright.

Where the threshold actually bites (measured, `rms`-controlled synthetics): floor + noise
σ = 0.05 → 0.034 anchors; σ = 0.1 → 0.066 anchors (3 % scale error); σ = 0.2 → 0.169
rejected; σ = 0.4 → 0.359 rejected.

### B3. The §3.3 ruling 2 defect, before and after

Ruling 2's scene: a wall across the forward arc at **1.2 m** with the right-hand crop open
at 6 m. Nothing is inside `D_STOP_M`, so every column is "passable" and the binary rule's
gap is the whole arc, centroid exactly 0.

| | `steer` | `gap_bearing_deg` | `proximity` | `blocked` |
|---|---|---|---|---|
| step 23 (anchored) | **0.0000** | 0.000 | 0.750 | false |
| step 27 (anchored) | **+0.5222** | +43.636 | 0.750 | false |
| step 27, mirrored | **−0.5222** | −43.636 | 0.750 | false |
| step 23 = 27 (unanchored) | +0.4082 | +60.000 | 0.500 | false |

The chosen run is *unchanged* — still `(0, 48)`, still plain-centroid 0.000 — which is what
shows the fix is the weighting and not a different gap (asserted in the test).

Step-23 brief §D case A, the same defect on a wall *plane* at 1.2 m with a 6 m open right:
`steer` **0.0000 → +0.4072**, bearing **0.000 → +26.542**, `proximity` 0.7497 unchanged.
Case C (L = 0.40 m): 0.5204 → 0.6650, bearing 24.375 → 39.805.

A pleasing cross-check: on case A the metric rule's new bearing (+26.542°) and the
scale-free rule's (+26.250°) now agree to a third of a column, from two independent
derivations.

### B4. The scale-free path is byte-identical (acceptance 5)

Pinned against the numbers step 23 *published* rather than against a re-derivation:

| case (step-23 brief §D) | `gapDirection` | bearing | `steer` | `proximity` |
|---|---|---|---|---|
| B — left wall plane 1.2 m | 0.291666… | 26.25 | 0.27003086243366084 | 0.5 |
| D — left wall plane 0.40 m | 0.3125 | 28.125 | 0.2795084971874737 | 0.5 |

Asserted at `rel=1e-12`. `relative_freedom`, `freedom_runs`, `UNANCHORED_PROXIMITY`,
`UNANCHORED_PASSABLE_FREEDOM` and `UNANCHORED_MIN_GAP_DEG` are untouched; the metric fork
lives in `steer_frame._ring_for` so the scale-free path assembles and smooths the same
bytes it did.

### B5. `proximityFactor` is now a real measurement

End to end over the wire, uniform scenes at 3.0 / 2.4 / 1.8 / 1.2 / 0.6 m give
`proximity` **0.00 / 0.25 / 0.50 / 0.75 / 1.00** — five distinct values where step 23 gave
the constant 0.5 five times. `abs(steer)` rises strictly monotonically as a wall plane
closes through 2.0 / 1.2 / 0.8 / 0.5 / 0.3 m, in metres.

## C. Two findings that changed the design, both measured

### C1. The affine pair is per **crop**, not per frame — six numbers, not two

Step 23's report said a fitted scale "must arrive as **two** numbers per frame". It is two
numbers **per crop**. Each forward crop is a separate inference, and Depth Anything V2's
affine freedom is per inference: the measured pairs from *one* frame are
(3.658, 2.509) / (0.905, 3.519) / (1.373, 3.810). Averaging them, or carrying one pair per
frame, would convert two crops in a third crop's units. `GapProfile` is per crop, so the
contract shape was already right; the *bridge* is what had to change —
`DepthAnchor.crop_scales`, and `column_distances` reading each column with its own crop's
pair.

### C2. The 48-column ring was not one measurement space, and the seams proved it

Falling out of C1: `smooth_ring` runs `SMOOTH_KERNEL` **across the crop seams** on purpose
(that is what makes a seam-straddling gap one gap) — but on *raw* depth, i.e. averaging two
different unit systems. Measured on a scene at a uniform 3 m with the real pairs, a seam
column came back as **2.03 m (a 32 % error)**, and because `d_min` is taken over the whole
arc that one column set `proximity` for the entire frame. This was not a footnote; it was
enough to move `proximity` from 0.42 to 0.60 on a uniform 2 m scene.

Fixed at the root rather than patched at the seams: on an accepted anchor each crop's
columns are converted with its own pair **before** assembly (`to_inverse_metres`), so all
48 columns are plain inverse metres, the kernel averages like with like, and the ring
carries no scale at all (`distance = 1/column`). A uniform scene is now uniform to 1e-9.
Inverse metres rather than metres because that is what the ring always smoothed, and
averaging inverse depth leans on the *nearer* neighbour — the conservative direction for a
hazard. Both conventions survive intact and are tested: a non-positive column stays
absence, and a column past the model's horizon saturates to `1/DISTANCE_MAX_M` rather than
to a zero that would read as absence.

**The same flaw is still present on the scale-free path, and was left there deliberately.**
`relative_freedom` cancels `a` and `b` exactly — *within one crop*. Across three crops with
three pairs it is comparing incommensurable numbers: under the measured pairs a scene at a
uniform 6 m reads 3.12 / 3.65 / 4.04 on C5 / C0 / C1, so the ranking can show "free on the
left, blocked on the right" for an empty corridor. Acceptance 5 requires the unanchored path
to be byte-identical, so this is **reported, not fixed** — see §F3. It is the strongest
argument for landing the metric path at the venue.

## D. Decisions the plan left open

1. **The weight is `d − D_STOP_M`, not `d`.** It vanishes exactly where passability does, so
   a barely-passable column is not somewhere to aim and a column joining a run at its edge
   barely moves the direction (tested: a column at `D_STOP_M + 1e-4` shifts the direction by
   < 0.01°). Weighting by `d` would let a run's near fringe pull as hard as its open middle.
2. **The run's score is the sum of free distance over its columns** — "choose the run by
   free distance, not by column count" (ruling 2), read as the integral of freedom over the
   gap rather than its maximum, so it degrades continuously into the column rule.
3. **The tie tolerance is one column's worth of the best run's own freedom.** That reduces
   *exactly* to step 23's "within `WIDTH_TIE_COLUMNS` of the widest" when every column of
   both runs is at the same distance, which is why both of step 23's tie tests keep their
   meaning and the scale-free path is unchanged. A relative epsilon guards the knife edge.
4. **`anchored` is the validity flag; presence of the pair is the second, weaker one.**
   "Two numbers plus validity" is read as: both-or-neither presence (enforced by a
   validator) plus the existing `anchored`. No third boolean — redundant state is a
   liability, and `anchored` already means exactly "this fit may be believed".
5. **`anchored = True` with no pair still parses, and the bridge refuses it.** An old cloud
   sends exactly that; 422-ing a legal older response is a worse failure than declining the
   anchor. The producer invariant lives in `CropDepthProfile.__post_init__` instead, so this
   process can never emit one. Verdict order is pinned by a test: a *rejected* pair reads as
   `PRODUCER_UNANCHORED`, as before, never as the new `NO_SCALE`.
6. **`GROUND_FIT_RESIDUAL_MAX = 1.0` in the bridge kept its value**, which under the new
   definition means "100 % distance error at 3 m" — still a sane blown-up-fit ceiling and
   still clearly looser than the producer's 0.15, so step 23's no-duplicate-judgement rule
   holds.

## E. What was tried and rejected, with the measurement

**A split-half gradient check.** The residual cannot see a partly occluded floor (§F2), and
fitting the near and far halves of the floor strip separately looked like a cheap
independent guard. Measured, it fails: the disagreement between halves is
**120.6 % on real crop 5** (its far half has `a` = −0.752), 35.4 % on crop 0 and 53.3 % on
crop 1 — *worse* than the 100 % scored by the synthetic occluded floor it was meant to
catch. Any threshold that rejected the occluded case would have rejected the real frame's
crop 5 and failed acceptance 1. Dropped, and the hole reported instead.

## F. Spec bugs, ambiguities and deviations

1. **Step 27 says "a contract amendment in step 21's style (schema + golden fixture + both
   parsers)". Two thirds of that do not apply, and I did not do them.** `GapProfile` is the
   **cloud↔bridge** contract: it lives in `backend/src/cane/cloud/api_models.py`, it has no
   JSON schema (`contracts/` holds only the phone envelope and awareness events), and it has
   **no Swift parser** — `grep -rl GapProfile ios/` is empty. `contracts/` is therefore
   untouched, deliberately: the `steer` frame already carries `proximity` as 0…1 and
   `proximity_anchored` beside it, so nothing the phone parses changed, and
   `contracts/README.md`'s steer prose is still accurate as written. Touching a frozen schema
   to record a change it does not describe would be the opposite of step 21's discipline.
   The equivalent rigour was applied where the contract does live: wire-JSON parse tests
   (not dict tests), byte-identical round-trips, backward compatibility both ways,
   ruling-5 integer literals, non-finite rejection, and a producer-side invariant.
2. **The residual guard is blind to a plane at the wrong angle or height, and to a partly
   occluded floor. Measured, and the largest open risk this step leaves.** A pitch error is
   absorbed exactly into `b`, a mount-height error exactly into `a`, and a constant up-slope
   into both — all three fit with a **zero** residual and return a wrong metre. Measured: a
   20 % mount-height error gives a 20 % distance error at residual 0.000; a 20 % up-slope
   gives a 17 % scale error at residual 0.000. Worse, a floor whose near half is covered by a
   crowd at 1.5 m inflated `a` **4×** (17.08 against a true 4.0) at residual 0.056 — *inside*
   the 0.15 ceiling — which reports everything as four times farther than it is. §2.3's
   `range_band` cross-check cannot catch it (ruling 4 is right: it needs ~12× before a band
   moves). **Measure `MOUNT_HEIGHT_M`, keep the camera level, and treat a crowded floor as a
   known failure mode for step 25.** Plan risk 1 anticipated this in prose; it now has
   numbers.
3. **`relative_freedom`'s cancellation is per crop, not per ring** (§C2). The live scale-free
   path can be biased by the model's per-crop affine freedom — measured spread 3.12 / 3.65 /
   4.04 for one constant distance. Left unfixed because acceptance 5 forbids changing it.
   The fix is the same one C2 applies to the metric path and would need its own acceptance.
4. **`ground_fit_residual`'s meaning changed while its name and field did not.** Same wire
   field, new definition (§B2). Anyone comparing a pre-step-27 residual to a post-step-27 one
   is comparing two different quantities; the conversion is `new = old · 0.0365 · 3.00`.
5. **`D_STOP_M`'s double duty is mitigated, not removed.** Ruling 2's prescription — a
   depth-weighted direction — makes the two halves of the law co-vary, which fixes the
   observable defect. The underlying oddity (one constant is both the passability threshold
   and the ramp's saturation point) is still there: a wall at 1.2 m is still formally
   "passable". Step 23 §E1 suggested a separate passability distance or a time-to-contact
   rule; ruling 2 chose the weighting instead, and this step implemented the ruling.
6. **`cane.scene`'s step-26 prose will go stale.** `src/cane/scene/gap_speech.py` states "in
   the field `anchored` is false" and builds its speech on that. With this step landed,
   `anchored` is **true** on a real frame, so that module's premise needs revisiting by
   whoever owns step 26. Flagged, not touched (another agent is mid-edit there).
7. **A uniform 1.2 m ring still emits `steer = 0.0`**, and that is now correct rather than a
   defect: 1.2 m in all 48 directions is a cylinder around the wearer, with no deepest part
   to lean toward. Step 23's test that pinned the defect was rewritten to say so, and the
   asymmetric scene it *should* have used is the new regression test.

## G. Tests

```
cd backend && uv run pytest tests/cloud tests/bridge tests/transport -q
  → 1025 passed, 1 skipped         (tests/cloud 437; tests/bridge + tests/transport 588/1)
cd backend && UV_PROJECT_ENVIRONMENT=.venv-vision \
    uv run --extra depth --extra vision python deploy/affine_ground_fit_check.py
  → ACCEPTANCE 1: PASS, 3 of 3 crops anchored
```

Was 433 + 588 before this step in those directories. `tests/scene` was not run — another
agent was mid-edit there — and nothing in this step touches it.

One pre-existing flake, unrelated: `tests/cloud/test_app.py::test_non_inference_overhead_p50_under_budget`
fails under machine load (20 ms budget, measured 21–27 ms at load ≈ 5 on 8 cores).
Verified by stashing this step's changes and re-running: it fails at **31.79 ms** on the
pre-change tree too.

### The RED that was confirmed first

Tests were written before any implementation and run: **46 failed, 29 passed**, plus three
modules that could not even be collected (`RESIDUAL_REFERENCE_M`, `free_distance_m`,
`DELTA_DEG_WARN` did not exist). Three of my own assertions were wrong and the code was
right — each was corrected to the measurement, not papered over:
a forgotten offset puts a 1.2 m wall at 0.66 m and not past 3 m; a three-column gap at 6 m
genuinely *is* body-wide (the width test is evaluated at the run's own distance); and a lone
sky column next to a 2 m wall is smoothed toward it, which is the kernel working.

| acceptance criterion | where |
|---|---|
| 1. affine fit reproduces 0.068/0.005/0.013, `anchored` on ≥ 2 of 3 | `deploy/affine_ground_fit_check.py` (real model, 3 of 3) + `test_affine_ground_fit.py::test_the_three_real_crops_anchor_under_the_new_threshold` (torch-free) |
| 2. planted scale recovered within 10 % | `test_affine_ground_fit.py::test_a_planted_affine_scale_is_recovered_within_ten_percent` (6 cases, three of them the measured pairs) |
| 3. §3.3 ruling 2 regression | `test_steering.py::test_a_wall_ahead_at_one_point_two_metres_steers_toward_the_open_side`, `…_the_mirror_of_that_wall…`, `test_steer_frame.py::test_the_ruling_two_wall_emits_a_non_zero_steer_over_the_wire` |
| 4. `proximityFactor` varies; `abs(steer)` rises in metres | `test_steer_frame.py::test_proximity_is_now_a_measurement_and_moves_with_the_distance`, `…_abs_steer_rises_as_a_wall_closes_in_metres_not_in_arbitrary_units` |
| 5. unanchored path byte-identical | `test_steering.py::test_the_unanchored_path_reproduces_step_23s_published_numbers` (step-23 §D cases B and D at `rel=1e-12`), `…_does_not_weight_by_depth` |
| 6. no scale → parses, falls back | `test_api_models.py::test_an_old_response_without_a_scale_still_parses`, `test_gap_anchor.py::test_an_old_cloud_that_sends_no_scale_cannot_be_anchored`, `test_steer_frame.py::test_an_old_cloud_with_no_scale_falls_back_to_the_unanchored_path` |
| 7. §1.1 still honoured | `test_affine_ground_fit.py::test_the_fit_never_looks_at_the_maps_own_range`, `…_a_scaled_frame_scales_the_recovered_parameters_rather_than_cancelling`; step 22's `test_depth_source_guards.py` (no `normalize_depth` anywhere in `cane.cloud`) and `test_depth_profile.py`'s three normalisation tests all still pass untouched |
| D. blind-wedge warning above 15° | `test_rungs_config.py::test_a_large_but_legal_mount_offset_names_the_blind_wedge` (4 offsets), `…_a_small_mount_offset_is_silent` (5), `…_the_warning_threshold_is_below_the_hard_failure` |

**On criterion 7 specifically.** The distinction is mechanical, not asserted by comment:
min-max normalisation reads both of its numbers off the depth map, so an empty corridor and
a wall become identical. This fit's right-hand side is `row_inverse_distance` — mount height
and focal length only — so poisoning the map's extremes cannot move either parameter, and
doubling the map doubles both parameters while leaving every recovered *distance* unchanged.
Both are tests. Nothing in the path normalises per frame.

## H. What is still unproven

* **No live X4 walk.** Every ring in the tests is scripted; the real-frame check is one
  still frame through the fit, not a walk. Whether the fit anchors *outdoors*, where a 60°
  crop sees the floor only between 2.42 m and 3.49 m, is step 25's to find — and plan risk 1
  already names it. If it is rejected at the venue, the scale-free path stands and
  `proximity_anchored = false` says so, exactly as before this step.
* **§F2's blind spots are unmeasured on real data.** Mount height, camera pitch and a
  crowded floor all produce a wrong metre at a clean residual. The synthetic magnitudes are
  above; nobody has measured the real mount height twice.
* **The L4.** The fit adds one more least-squares solve over ≤ 98 medians per crop, so the
  cost is unchanged in any way that matters (step 22 measured the head's whole arithmetic at
  0.56 ms/crop against ~30 ms of inference), but that is an argument, not a measurement on
  the L4.
