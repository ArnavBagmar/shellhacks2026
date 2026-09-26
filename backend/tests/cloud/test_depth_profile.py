"""Band -> sixteen raw column means -> ground anchor (plan Sec 1.1, 2.3, 4.2).

The trap this file exists to pin is plan Sec 1.1: per-frame min-max
normalisation would make an empty corridor score identically to a wall,
because the nearest thing in frame always normalises to 1.0. The columns are
therefore RAW relative inverse depth, and
`test_a_constant_depth_offset_changes_the_columns` fails the moment anyone
"helpfully" rescales them.
"""

from __future__ import annotations

import numpy as np
import pytest

from cane.cloud.api_models import COLUMNS_PER_CROP, GapProfile
from cane.cloud.depth.constants import (
    DEFAULT_MAX_GROUND_RESIDUAL,
    MOUNT_HEIGHT_M,
    RESIDUAL_CAP,
)
from cane.cloud.depth.geometry import band_rows
from cane.cloud.depth.profile import (
    CropDepthProfile,
    build_profile,
    column_means,
    fit_ground,
)

from .depth_synthetic import CROP_PX, flat, ideal_ground, wall_on_the_left, wall_on_the_right

MAX_RESIDUAL = DEFAULT_MAX_GROUND_RESIDUAL


def profile(depth: np.ndarray, crop_index: int = 0):
    built = build_profile(crop_index, depth, max_residual=MAX_RESIDUAL)
    assert built is not None
    return built


# --------------------------------------------------------------------------
# Sixteen columns of raw relative depth
# --------------------------------------------------------------------------


def test_there_are_exactly_sixteen_finite_float_columns() -> None:
    built = profile(wall_on_the_left())
    assert len(built.columns) == COLUMNS_PER_CROP == 16
    assert all(isinstance(value, float) for value in built.columns)
    assert np.isfinite(built.columns).all()


def test_a_wall_on_the_left_reads_nearer_on_the_left() -> None:
    """Acceptance criterion 1. Depth Anything V2 returns relative *inverse*
    depth, so nearer is LARGER; a left wall must therefore give a strictly
    decreasing column profile, left to right.
    """
    columns = profile(wall_on_the_left()).columns
    assert columns[0] > columns[-1]
    assert all(a > b for a, b in zip(columns, columns[1:])), columns


def test_a_wall_on_the_right_is_the_exact_mirror() -> None:
    left = profile(wall_on_the_left()).columns
    right = profile(wall_on_the_right()).columns
    assert all(a > b for a, b in zip(right[1:], right)), right
    assert right == pytest.approx(tuple(reversed(left)), rel=1e-6)


def test_each_column_is_the_mean_of_its_own_slice_of_the_band() -> None:
    """The columns are means over the *kept band only*: rows outside it must
    not leak in, or the ceiling and the floor vote on the steering.
    """
    depth = wall_on_the_left()
    rows = band_rows(CROP_PX)
    assert rows is not None
    top, bottom = rows
    expected = column_means(depth[top:bottom, :])
    assert profile(depth).columns == expected
    # Poison everything outside the band; the columns must not move.
    poisoned = depth.copy()
    poisoned[:top, :] = 900.0
    poisoned[bottom:, :] = -900.0
    assert profile(poisoned).columns == expected


def test_column_means_split_the_width_evenly() -> None:
    """One column per 3.75 degrees of the crop's 60. An uneven split would
    shift every bearing in step 23's 48-column ring.
    """
    band = np.arange(CROP_PX, dtype=np.float32)[None, :].repeat(4, axis=0)
    columns = column_means(band)
    width = CROP_PX // COLUMNS_PER_CROP
    assert width == 40
    for index, value in enumerate(columns):
        lo, hi = index * width, (index + 1) * width
        assert value == pytest.approx(float(np.mean(band[:, lo:hi])))


# --------------------------------------------------------------------------
# Plan Sec 1.1: the normalisation trap
# --------------------------------------------------------------------------


def test_a_constant_depth_offset_changes_the_columns() -> None:
    """Acceptance criterion 2, and the whole reason `columns` is unbounded.

    Two frames differing only by a constant offset are an empty corridor and a
    wall. Under per-frame min-max normalisation both would come back as the
    same sixteen numbers; raw means differ by exactly the offset.
    """
    offset = 0.75
    near = profile(wall_on_the_left())
    far = profile(wall_on_the_left() + np.float32(offset))
    assert near.columns != far.columns
    for close, distant in zip(near.columns, far.columns):
        assert distant - close == pytest.approx(offset, abs=1e-5)


def test_a_flat_frame_keeps_its_own_level() -> None:
    """A featureless wall at two distances. Min-max normalisation collapses
    both to zeros (`normalize_depth` returns exactly that for a zero span);
    raw means keep the level that tells them apart.
    """
    assert profile(flat(2.5)).columns == pytest.approx((2.5,) * COLUMNS_PER_CROP)
    assert profile(flat(0.4)).columns == pytest.approx((0.4,) * COLUMNS_PER_CROP)


def test_columns_are_not_rescaled_by_a_multiplicative_factor_either() -> None:
    """Scale invariance is the other half of the same trap: doubling the
    inverse depth must double the columns, not re-normalise them away.
    """
    single = profile(wall_on_the_left()).columns
    doubled = profile(wall_on_the_left() * np.float32(2.0)).columns
    assert doubled == pytest.approx(tuple(2.0 * value for value in single), rel=1e-6)


# --------------------------------------------------------------------------
# Plan Sec 2.3: the ground-plane scale anchor
# --------------------------------------------------------------------------


def test_a_perfect_ground_plane_anchors_with_a_near_zero_residual() -> None:
    fit = fit_ground(ideal_ground(scale=4.0), CROP_PX, max_residual=MAX_RESIDUAL)
    assert fit.anchored is True
    assert fit.residual == pytest.approx(0.0, abs=1e-3)
    assert fit.scale == pytest.approx(4.0, rel=1e-3)
    assert fit.rows_used >= 8


def test_the_fitted_scale_recovers_metres_from_relative_depth() -> None:
    """What the anchor is *for*: `d_m = scale / z`. Read a raw value out of the
    synthetic floor and check the fitted scale turns it back into the metres
    the pinhole geometry says that row is looking at.
    """
    depth = ideal_ground(scale=4.0)
    fit = fit_ground(depth, CROP_PX, max_residual=MAX_RESIDUAL)
    assert fit.scale is not None
    focal_640 = 320.0 / np.tan(np.radians(30.0))
    for row in (560, 578, 600, 630):
        geometric_d = focal_640 * MOUNT_HEIGHT_M / (row - 319.5)
        assert fit.scale / float(depth[row, 100]) == pytest.approx(geometric_d, rel=1e-3)


def test_a_floor_covered_by_a_wall_is_rejected_not_guessed() -> None:
    """Plan Sec 2.3's first guard, and plan Sec 4.2's no-silent-fake rule: a
    vertical surface filling the floor rows has no depth gradient down the
    frame, so there is no scale to be had and `anchored` must say so.
    """
    for depth in (flat(2.0), wall_on_the_left(), wall_on_the_right()):
        fit = fit_ground(depth, CROP_PX, max_residual=MAX_RESIDUAL)
        assert fit.anchored is False
        assert fit.residual > MAX_RESIDUAL


def test_a_noisy_floor_is_rejected() -> None:
    rng = np.random.default_rng(7)
    noise = rng.uniform(1.9, 3.0, size=(CROP_PX, CROP_PX)).astype(np.float32)
    fit = fit_ground(noise, CROP_PX, max_residual=MAX_RESIDUAL)
    assert fit.anchored is False


def test_an_inverted_ground_gradient_is_rejected() -> None:
    """If the model ever returned true depth instead of inverse depth the
    gradient flips sign; a negative scale is physically meaningless and must
    never be shipped as an anchor.
    """
    fit = fit_ground(-ideal_ground(scale=4.0), CROP_PX, max_residual=MAX_RESIDUAL)
    assert fit.anchored is False
    assert fit.scale is None


def test_the_residual_is_finite_dimensionless_and_capped() -> None:
    """`ground_fit_residual` is a ratio, not a depth: the *columns* may never
    be normalised (Sec 1.1), but a goodness-of-fit that carried the model's
    arbitrary scale could not be thresholded at all.
    """
    for scale in (0.5, 4.0, 400.0):
        fit = fit_ground(ideal_ground(scale=scale), CROP_PX, max_residual=MAX_RESIDUAL)
        assert fit.anchored is True and fit.residual == pytest.approx(0.0, abs=1e-3)
    capped = fit_ground(flat(2.0), CROP_PX, max_residual=MAX_RESIDUAL)
    assert 0.0 <= capped.residual <= RESIDUAL_CAP
    assert np.isfinite(capped.residual)


def test_an_affine_offset_of_any_size_now_anchors() -> None:
    """The margin this test used to measure no longer exists, which is the point.

    Step 22 fitted one parameter and could only tolerate a small `b`: 0.28
    anchored, 1.40 did not. The real `b` measured 2.5..3.8, so every real crop
    was rejected -- correctly, because rejecting beats shipping a bad scale.
    Step 27 fits `b` instead of tolerating it, so there is no margin to run out
    of, and the offset comes back in the answer rather than in the residual.
    Its own coverage is in `test_affine_ground_fit.py`.
    """
    ideal = ideal_ground(scale=4.0)
    for offset in (0.28, 1.40, 3.81):
        fit = fit_ground(ideal + np.float32(offset), CROP_PX, max_residual=MAX_RESIDUAL)
        assert fit.anchored is True, offset
        assert fit.offset == pytest.approx(offset, abs=1e-3), offset
        assert fit.scale == pytest.approx(4.0, rel=1e-3), offset


# --------------------------------------------------------------------------
# The profile as a whole, and its refusals
# --------------------------------------------------------------------------


def test_a_profile_converts_to_a_valid_gap_profile() -> None:
    depth = ideal_ground(base=wall_on_the_left(), from_row=542)
    built = profile(depth, crop_index=5)
    wire = built.to_gap_profile()
    assert isinstance(wire, GapProfile)
    assert wire.crop_index == 5
    assert wire.band_rows == band_rows(CROP_PX)
    assert wire.anchored is True
    assert len(wire.columns) == COLUMNS_PER_CROP
    assert all(a > b for a, b in zip(wire.columns, wire.columns[1:]))
    # Round-trips through the wire unchanged: the columns are plain floats.
    assert GapProfile.model_validate_json(wire.model_dump_json()) == wire


def test_the_wire_now_carries_the_scale_the_bridge_needs() -> None:
    """The gap step 22 reported, closed by step 27: both numbers reach the wire,
    so the bridge can convert raw columns into metres from the response alone.
    """
    built = profile(ideal_ground(base=wall_on_the_left(), from_row=542))
    wire = built.to_gap_profile()

    assert built.ground_scale == pytest.approx(4.0, rel=1e-3)
    assert wire.ground_scale == built.ground_scale
    assert wire.ground_offset == built.ground_offset
    assert wire.has_affine_scale


def test_a_profile_may_never_claim_an_anchor_it_cannot_transmit() -> None:
    """A producer invariant, checked where the profile is built rather than on
    the wire: `anchored` without the pair is a claim the bridge cannot act on.
    The *contract* still parses such a response, because an old cloud sends
    exactly that and a 422 on a legal older response is the worse failure.
    """
    with pytest.raises(ValueError, match="anchored with no affine pair"):
        CropDepthProfile(
            crop_index=0,
            columns=(1.0,) * COLUMNS_PER_CROP,
            band_rows=(171, 542),
            ground_fit_residual=0.0,
            anchored=True,
            ground_scale=None,
            ground_offset=None,
        )


def test_a_non_finite_depth_map_yields_no_profile() -> None:
    """A NaN from an fp16 overflow must be absence, not a 422 on our own
    response (the contract rejects non-finite columns) and not a zero.
    """
    for bad in (np.nan, np.inf, -np.inf):
        depth = wall_on_the_left()
        depth[300, 17] = np.float32(bad)
        assert build_profile(0, depth, max_residual=MAX_RESIDUAL) is None


def test_an_empty_or_wrongly_shaped_depth_map_is_a_programming_error() -> None:
    with pytest.raises(ValueError):
        build_profile(0, np.zeros((0, 0), dtype=np.float32), max_residual=MAX_RESIDUAL)
    with pytest.raises(ValueError):
        build_profile(0, np.zeros((8, 8, 3), dtype=np.float32), max_residual=MAX_RESIDUAL)


def test_a_band_narrower_than_sixteen_columns_yields_no_profile() -> None:
    """Sixteen means over four pixels would be twelve empty slices: NaN
    columns that the contract rejects, from a mean of an empty array. Absence
    is the only honest answer.
    """
    assert build_profile(0, np.zeros((4, 4), dtype=np.float32), max_residual=MAX_RESIDUAL) is None


def test_the_ground_fit_reads_the_floor_of_the_frame_it_was_given() -> None:
    """A 512-px rung has its own band and its own floor rows; the fit must
    follow the decoded frame, never a remembered 640.
    """
    fit = fit_ground(ideal_ground(crop_px=512, scale=4.0), 512, max_residual=MAX_RESIDUAL)
    assert fit.anchored is True
    assert fit.scale == pytest.approx(4.0, rel=1e-3)


def test_a_column_slice_of_a_16_wide_band_still_yields_16_columns() -> None:
    """`crop_px` is always a multiple of 32, so the split is exact -- but a
    band only as wide as the column count must not produce 15 or 17.
    """
    assert len(column_means(np.ones((3, COLUMNS_PER_CROP), dtype=np.float32))) == COLUMNS_PER_CROP
    assert len(column_means(np.ones((3, 33), dtype=np.float32))) == COLUMNS_PER_CROP


def test_mount_height_is_read_from_the_constant_not_inlined() -> None:
    """A per-wearer number: it is measured at build, so it must be changeable
    in one place. Fitting a 1.40 m mount with 0.70 m halves the scale.
    """
    depth = ideal_ground(scale=4.0)
    fit = fit_ground(depth, CROP_PX, max_residual=MAX_RESIDUAL, mount_height_m=MOUNT_HEIGHT_M / 2.0)
    assert fit.scale == pytest.approx(2.0, rel=1e-3)
