"""The two-parameter ground fit: `z = a*(1/d) + b` (step 27, plan Sec 2.5).

Plan Sec 2.3 prescribed a one-parameter fit and step 22 measured it failing on
every forward crop of a real X4 frame, because **Depth Anything V2 is
affine-invariant in disparity**: its output is `a/d + b` with both unknowns
moving per inference, and the measured offset dominates (`b` 2.5..3.8 against
`a` 0.9..3.7). This file is the fit that recovers both, and the guard that
decides when it may be believed.

Why this is not the Sec 1.1 trap. Per-frame min-max normalisation reads its
two numbers **off the depth map itself** -- the nearest thing in frame becomes
1.0, so an empty corridor and a wall come back identical. This fit reads its
two numbers off the **ground plane**: every `1/d` on the right-hand side comes
from a measured mount height and a known focal length through
`row_inverse_distance`, and nothing in it depends on the map's own minimum or
maximum. Swap the whole depth map for a different scene and the design matrix
does not move. `test_the_fit_never_looks_at_the_maps_own_range` pins that.
"""

from __future__ import annotations

import numpy as np
import pytest

from cane.cloud.depth.constants import (
    DEFAULT_MAX_GROUND_RESIDUAL,
    MOUNT_HEIGHT_M,
    RESIDUAL_CAP,
    RESIDUAL_REFERENCE_M,
)
from cane.cloud.depth.geometry import ground_rows, row_inverse_distance
from cane.cloud.depth.profile import fit_ground

from .depth_synthetic import CROP_PX, affine_ground, flat, ideal_ground, wall_on_the_left

MAX_RESIDUAL = DEFAULT_MAX_GROUND_RESIDUAL

#: Measured on `docs/steps/assets/step-06-live-equirect.jpg`, dewarped by
#: `cane.bridge.dewarp` to three 640-px crops and run through Depth Anything V2
#: Small at `input_size = 280` (step 22's own loader). One row per forward crop:
#: `(crop_index, fitted a, fitted b, raw rms of the affine fit)`. The rms column
#: reproduces plan Sec 2.5's 0.068 / 0.005 / 0.013 exactly.
#: Re-measure with `deploy/affine_ground_fit_check.py`.
MEASURED_REAL_CROPS: tuple[tuple[int, float, float, float], ...] = (
    (5, 3.658, 2.509, 0.0679),
    (0, 0.905, 3.519, 0.0049),
    (1, 1.373, 3.810, 0.0133),
)
#: The same three crops under the **one-parameter** fit step 22 shipped: its
#: residual, in its own units, against its own 0.25 ceiling. All three were
#: rejected, which is the measurement this whole step exists to answer.
MEASURED_ONE_PARAMETER_RESIDUALS = (0.686, 0.922, 0.892)
#: `std(1/d)` over the ground rows. A constant of the crop geometry, not of the
#: frame: `1/d = (row - horizon) / (focal_px * h)` and both the numerator and
#: `focal_px` scale with `crop_px`, so it is the same for every rung.
MEASURED_GROUND_STD_INVERSE_D = 0.03646


def _ground_inverse_distance(crop_px: int = CROP_PX) -> np.ndarray:
    rows = ground_rows(crop_px)
    assert rows is not None
    return row_inverse_distance(np.arange(rows[0], rows[1], dtype=np.float64), crop_px)


# --------------------------------------------------------------------------
# Recovering both parameters
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("scale", "offset"),
    [(4.0, 0.0), (4.0, 2.5), (3.658, 2.509), (0.905, 3.519), (1.373, 3.810), (0.5, -1.25)],
)
def test_a_planted_affine_scale_is_recovered_within_ten_percent(
    scale: float, offset: float
) -> None:
    """Step 27 acceptance: a synthetic frame with a known planted scale
    recovers `a` and `b` within 10 %. Three of the cases are the offsets
    measured on the real frame, so the recovery is exercised where it matters.
    """
    fit = fit_ground(affine_ground(scale=scale, offset=offset), CROP_PX, max_residual=MAX_RESIDUAL)

    assert fit.scale is not None and fit.offset is not None
    assert fit.scale == pytest.approx(scale, rel=0.10)
    assert fit.offset == pytest.approx(offset, rel=0.10, abs=0.10)
    assert fit.anchored is True


def test_the_offset_the_one_parameter_fit_could_not_see_is_now_recovered_exactly() -> None:
    """The measured failure, inverted. Step 22's fit assumed `b = 0`; the real
    `b` was 2.5..3.8, which is why it was rejected. Here the same offset is
    recovered to the float and the residual is zero.
    """
    fit = fit_ground(affine_ground(scale=3.658, offset=2.509), CROP_PX, max_residual=MAX_RESIDUAL)

    assert fit.scale == pytest.approx(3.658, rel=1e-4)
    assert fit.offset == pytest.approx(2.509, rel=1e-4)
    assert fit.rms == pytest.approx(0.0, abs=1e-4)
    assert fit.residual == pytest.approx(0.0, abs=1e-4)


def test_an_offset_no_longer_costs_the_anchor_at_any_size() -> None:
    """The one-parameter fit anchored at `b = 0.28` and failed at `b = 1.40`
    (step 22's `test_a_modest_affine_offset_still_anchors` measured that
    margin). The affine fit has no such margin to run out of.
    """
    for offset in (0.0, 0.28, 1.40, 2.509, 3.810, 10.0):
        fit = fit_ground(affine_ground(scale=4.0, offset=offset), CROP_PX, max_residual=MAX_RESIDUAL)
        assert fit.anchored is True, offset
        assert fit.scale == pytest.approx(4.0, rel=1e-3), offset
        assert fit.offset == pytest.approx(offset, abs=1e-3), offset


def test_the_recovered_pair_turns_a_raw_depth_back_into_metres() -> None:
    """What the fit is *for*: `d = a / (z - b)`. Read raw values out of the
    synthetic floor and check the recovered pair returns the metres the pinhole
    geometry says those rows are looking at.
    """
    scale, offset = 4.0, 2.5
    depth = affine_ground(scale=scale, offset=offset)
    fit = fit_ground(depth, CROP_PX, max_residual=MAX_RESIDUAL)
    assert fit.scale is not None and fit.offset is not None

    focal_640 = 320.0 / np.tan(np.radians(30.0))
    for row in (560, 578, 600, 630):
        geometric_d = focal_640 * MOUNT_HEIGHT_M / (row - 319.5)
        recovered = fit.scale / (float(depth[row, 100]) - fit.offset)
        assert recovered == pytest.approx(geometric_d, rel=1e-3), row


def test_the_scale_follows_the_mount_height_and_the_offset_does_not() -> None:
    """`a` carries the metre and so must track `MOUNT_HEIGHT_M`; `b` is the
    model's own zero and is a property of the inference, not of the mount.

    The consequence is worth stating: a mount height that is wrong by a factor
    scales every recovered distance by that factor **with a zero residual**, so
    this guard cannot catch it. Measure the height.
    """
    depth = affine_ground(scale=4.0, offset=2.5)
    halved = fit_ground(
        depth, CROP_PX, max_residual=MAX_RESIDUAL, mount_height_m=MOUNT_HEIGHT_M / 2.0
    )

    assert halved.scale == pytest.approx(2.0, rel=1e-3)
    assert halved.offset == pytest.approx(2.5, abs=1e-3)
    assert halved.residual == pytest.approx(0.0, abs=1e-4)


def test_the_fit_reads_the_floor_of_the_frame_it_was_given() -> None:
    """A 512-px rung has its own floor rows; neither parameter may come from a
    remembered 640.
    """
    fit = fit_ground(
        affine_ground(crop_px=512, scale=4.0, offset=2.5), 512, max_residual=MAX_RESIDUAL
    )

    assert fit.anchored is True
    assert fit.scale == pytest.approx(4.0, rel=1e-3)
    assert fit.offset == pytest.approx(2.5, abs=1e-3)


# --------------------------------------------------------------------------
# Plan Sec 1.1: this is geometric recovery, not normalisation
# --------------------------------------------------------------------------


def test_the_fit_never_looks_at_the_maps_own_range() -> None:
    """The §1.1 distinction, made mechanical.

    Min-max normalisation is `(z - z.min()) / (z.max() - z.min())`: both of its
    numbers come off the map. This fit's right-hand side is `row_inverse_distance`
    -- mount height and focal length only -- so **changing the map's extremes
    without changing the floor cannot move either parameter.** Poison the sky
    and the band with absurd values and the fit does not budge.
    """
    depth = affine_ground(scale=4.0, offset=2.5)
    rows = ground_rows(CROP_PX)
    assert rows is not None
    clean = fit_ground(depth, CROP_PX, max_residual=MAX_RESIDUAL)

    poisoned = depth.copy()
    poisoned[: rows[0] // 2, :] = np.float32(-900.0)
    poisoned[rows[0] // 2 : rows[0], :] = np.float32(900.0)
    fouled = fit_ground(poisoned, CROP_PX, max_residual=MAX_RESIDUAL)

    assert (fouled.scale, fouled.offset) == (clean.scale, clean.offset)
    assert fouled.residual == clean.residual


def test_a_scaled_frame_scales_the_recovered_parameters_rather_than_cancelling() -> None:
    """Under min-max normalisation, `2*z` and `z` are the same picture. Under a
    geometric fit they are not: both parameters double, and the *distances* the
    pair recovers are unchanged, which is the whole point.
    """
    single = fit_ground(affine_ground(scale=4.0, offset=2.5), CROP_PX, max_residual=MAX_RESIDUAL)
    doubled = fit_ground(affine_ground(scale=8.0, offset=5.0), CROP_PX, max_residual=MAX_RESIDUAL)

    assert doubled.scale == pytest.approx(2.0 * single.scale)
    assert doubled.offset == pytest.approx(2.0 * single.offset)
    assert doubled.residual == pytest.approx(single.residual, abs=1e-6)


# --------------------------------------------------------------------------
# The guard: what the residual is, and what it refuses
# --------------------------------------------------------------------------


def test_the_residual_is_the_distance_error_the_misfit_implies() -> None:
    """The definition, and why it changed.

    Step 22's residual was `rms / (a * std(1/d))` -- a ratio, thresholdable,
    but in no unit anyone can argue about. Since `d = a/(z - b)` gives
    `dd/d = -(d/a) dz`, the same misfit expressed as a **fractional distance
    error at `RESIDUAL_REFERENCE_M`** is `rms * d / a`: still dimensionless,
    still a ratio, and now a claim about metres.
    """
    scale, rms = 4.0, 0.08
    depth = affine_ground(scale=scale, offset=2.5)
    rows = ground_rows(CROP_PX)
    assert rows is not None
    # A +-rms sawtooth on the row medians: its mean is zero, so it barely moves
    # the least-squares line and shows up almost entirely as misfit.
    sign = np.where(np.arange(rows[0], rows[1]) % 2 == 0, 1.0, -1.0)
    depth[rows[0] : rows[1], :] += (rms * sign).astype(np.float32)[:, None]

    fit = fit_ground(depth, CROP_PX, max_residual=MAX_RESIDUAL)

    assert fit.scale == pytest.approx(scale, rel=0.02)
    assert fit.rms == pytest.approx(rms, rel=0.02)
    assert fit.residual == pytest.approx(fit.rms * RESIDUAL_REFERENCE_M / fit.scale, rel=1e-6)
    assert RESIDUAL_REFERENCE_M == 3.00


def test_the_threshold_is_the_distance_error_step_22_refused() -> None:
    """The derivation of the new number, in one assertion.

    Step 22 refused its own one-parameter scale because it "would have placed a
    row whose true distance is 3.49 m at 2.92 m, a 16 % error". `0.15` is just
    inside that: a fit whose misfit implies a worse-than-16 %-class error at the
    far end of the steering ramp is exactly the fit step 22 was right to refuse.
    """
    assert DEFAULT_MAX_GROUND_RESIDUAL == 0.15
    assert DEFAULT_MAX_GROUND_RESIDUAL < abs(2.92 - 3.49) / 3.49


def test_the_three_real_crops_anchor_under_the_new_threshold() -> None:
    """Step 27 acceptance 1, without torch: the measured `(a, rms)` pairs turned
    into verdicts by the shipped definition. The real-model end of this is
    `deploy/affine_ground_fit_check.py`.
    """
    residuals = [
        rms * RESIDUAL_REFERENCE_M / scale for _, scale, _, rms in MEASURED_REAL_CROPS
    ]

    assert residuals == pytest.approx([0.0557, 0.0164, 0.0291], abs=5e-4)
    assert all(value <= DEFAULT_MAX_GROUND_RESIDUAL for value in residuals)
    # 2.7x of margin on the worst of the three, so this is not a threshold
    # fitted to the last decimal of one frame.
    assert DEFAULT_MAX_GROUND_RESIDUAL / max(residuals) > 2.5


def test_step_22s_threshold_would_still_have_rejected_two_of_the_three() -> None:
    """Why the threshold had to be re-derived and not merely re-used.

    `0.25` of `rms / (a * std(1/d))` is `0.25 * std(1/d) * D_CLEAR` of the new
    residual -- an implicit demand for **2.7 % distance accuracy at 3 m**, which
    no real crop meets even with the right model. Re-using it would have
    anchored one crop of three and failed acceptance 1.
    """
    old_threshold_in_new_units = 0.25 * MEASURED_GROUND_STD_INVERSE_D * RESIDUAL_REFERENCE_M

    assert old_threshold_in_new_units == pytest.approx(0.0273, abs=1e-3)
    residuals = [rms * RESIDUAL_REFERENCE_M / scale for _, scale, _, rms in MEASURED_REAL_CROPS]
    assert sum(value > old_threshold_in_new_units for value in residuals) == 2
    # And the one-parameter fit's own residuals were all above its ceiling.
    assert all(value > 0.25 for value in MEASURED_ONE_PARAMETER_RESIDUALS)


def test_the_ground_rows_inverse_distance_spread_is_a_geometric_constant() -> None:
    """`std(1/d)` is the same for every rung, which is what lets the two
    thresholds above be compared at all.

    `1/d = (row - horizon) / (focal_px * h)` and both the numerator and
    `focal_px` scale linearly with `crop_px`, so the spread is exactly
    scale-free -- up to the half-row rounding of the band boundary, which is
    what the 1 % tolerance is (0.0363 at 256 px against 0.0365 at 640).
    """
    for crop_px in (256, 512, 640, 1280):
        spread = float(np.std(_ground_inverse_distance(crop_px)))
        assert spread == pytest.approx(MEASURED_GROUND_STD_INVERSE_D, rel=0.01), crop_px


@pytest.mark.parametrize("value", [2.0, 0.4, 5.844])
def test_a_wall_filling_the_floor_rows_yields_no_fit_at_all(value: float) -> None:
    """A vertical surface has no depth gradient down the frame, so there is no
    `a` to be had. Refused structurally -- `a > 0` -- rather than by a
    threshold, which matters because two free parameters fit a flat strip
    *perfectly* (`a = 0`, `b = const`, rms 0): a residual test alone would have
    anchored a wall.
    """
    fit = fit_ground(flat(value), CROP_PX, max_residual=MAX_RESIDUAL)

    assert fit.scale is None and fit.offset is None
    assert fit.anchored is False
    assert fit.residual == RESIDUAL_CAP


def test_a_tilted_wall_is_refused_by_the_gradients_sign() -> None:
    """A wall leaning either way still recedes *upward*, not downward, so the
    fitted gradient is negative. Measured across tilts 0.02..0.30.
    """
    u = _ground_inverse_distance()
    rows = ground_rows(CROP_PX)
    assert rows is not None
    for tilt in (0.02, 0.05, 0.10, 0.30):
        distances = 3.0 - tilt * (1.0 / u - np.mean(1.0 / u))
        depth = flat(0.0)
        depth[rows[0] : rows[1], :] = (4.0 / distances + 2.5).astype(np.float32)[:, None]
        fit = fit_ground(depth, CROP_PX, max_residual=MAX_RESIDUAL)
        assert fit.scale is None, tilt
        assert fit.anchored is False, tilt


def test_a_lateral_ramp_is_a_wall_to_this_fit_not_a_floor() -> None:
    """`wall_on_the_left` varies across columns and not down rows: the row
    medians are constant, so it is the flat case again.
    """
    fit = fit_ground(wall_on_the_left(), CROP_PX, max_residual=MAX_RESIDUAL)

    assert fit.anchored is False
    assert fit.scale is None


def test_a_noisy_floor_is_rejected_when_the_noise_swamps_the_geometry() -> None:
    """Where the threshold actually bites. Measured: sigma 0.1 depth units
    scores 0.066 and anchors (a 3 % scale error), sigma 0.2 scores 0.169 and
    does not.
    """
    rng = np.random.default_rng(11)
    rows = ground_rows(CROP_PX)
    assert rows is not None

    def noisy(sigma: float) -> float:
        depth = affine_ground(scale=4.0, offset=2.5)
        noise = rng.normal(0.0, sigma, size=(rows[1] - rows[0], 1)).astype(np.float32)
        depth[rows[0] : rows[1], :] += noise
        return fit_ground(depth, CROP_PX, max_residual=MAX_RESIDUAL)

    assert noisy(0.05).anchored is True
    swamped = noisy(0.4)
    assert swamped.anchored is False
    assert swamped.residual > MAX_RESIDUAL


def test_uniform_noise_over_the_whole_frame_never_anchors() -> None:
    rng = np.random.default_rng(7)
    noise = rng.uniform(1.9, 3.0, size=(CROP_PX, CROP_PX)).astype(np.float32)

    assert fit_ground(noise, CROP_PX, max_residual=MAX_RESIDUAL).anchored is False


def test_an_inverted_gradient_is_the_wrong_model_not_a_bad_fit() -> None:
    """If the model ever returned true depth instead of inverse depth the
    gradient flips; a negative `a` is physically meaningless and must never be
    shipped as an anchor.
    """
    fit = fit_ground(-ideal_ground(scale=4.0), CROP_PX, max_residual=MAX_RESIDUAL)

    assert fit.scale is None and fit.offset is None
    assert fit.anchored is False


def test_the_residual_is_finite_and_capped_however_bad_the_fit_is() -> None:
    for depth in (flat(2.0), wall_on_the_left(), -ideal_ground(scale=4.0)):
        fit = fit_ground(depth, CROP_PX, max_residual=MAX_RESIDUAL)
        assert np.isfinite(fit.residual)
        assert 0.0 <= fit.residual <= RESIDUAL_CAP


def test_too_few_floor_rows_is_absence_not_a_fit() -> None:
    fit = fit_ground(flat(2.0, crop_px=32), 32, max_residual=MAX_RESIDUAL)

    assert fit.scale is None and fit.offset is None
    assert fit.anchored is False


def test_a_fit_that_is_rejected_still_reports_both_numbers_when_it_has_them() -> None:
    """A rejected *fit* is not a missing fit: the numbers travel so the bridge
    can log them, and `anchored = False` is what stops them being believed.
    """
    rows = ground_rows(CROP_PX)
    assert rows is not None
    depth = affine_ground(scale=4.0, offset=2.5)
    # rms 0.25 against a fitted 4.0 is a residual of 0.1875 -- above the 0.15
    # ceiling, and deterministic, unlike a random draw whose gradient can flip.
    sign = np.where(np.arange(rows[0], rows[1]) % 2 == 0, 1.0, -1.0)
    depth[rows[0] : rows[1], :] += (0.25 * sign).astype(np.float32)[:, None]

    fit = fit_ground(depth, CROP_PX, max_residual=MAX_RESIDUAL)

    assert fit.anchored is False
    assert fit.residual > MAX_RESIDUAL
    assert fit.scale is not None and fit.offset is not None
