"""The vertical obstacle-band filter, checked against hand arithmetic (plan Sec 2.3).

The band is the one piece of step 22 that is pure geometry, so it is the one
piece that can be wrong *silently*: a band shifted by a hundred rows still
produces sixteen plausible column means, and nothing downstream can tell. So
every number here is derived independently of the implementation -- from
`math.tan` and the plan's `dRow = focal_px * h / d` -- and compared against it.
"""

from __future__ import annotations

import math

import numpy as np
import pytest

from cane.bridge.dewarp import CROP_FOV_DEG, focal_px
from cane.cloud.api_models import CROP_PX_MAX, CROP_PX_MIN, CROP_PX_MULTIPLE_OF
from cane.cloud.depth.constants import (
    BAND_REFERENCE_M,
    D_CLEAR_M,
    DEPTH_CROPS,
    MIN_GROUND_ROWS,
    MOUNT_HEIGHT_M,
    OBSTACLE_BAND_M,
)
from cane.cloud.depth.geometry import (
    band_rows,
    ground_rows,
    horizon_row,
    row_for_height,
    row_inverse_distance,
)

CROP_PX = 640
ALL_CROP_SIZES = tuple(range(CROP_PX_MIN, CROP_PX_MAX + 1, CROP_PX_MULTIPLE_OF))


def test_the_constants_are_the_plans_constants() -> None:
    """Plan Sec 3's table, verbatim. `DEPTH_CROPS` is 5, 0, 1 in *that* order:
    yaw centres -60, 0, +60, so the profiles read left to right.
    """
    assert DEPTH_CROPS == (5, 0, 1)
    assert MOUNT_HEIGHT_M == 1.40
    assert OBSTACLE_BAND_M == (0.20, 2.20)
    # The band is a rectangle in (row, distance) space, so it needs one
    # reference distance; the plan names none, and D_CLEAR is the farthest
    # distance the steering ramp reacts to at all.
    assert BAND_REFERENCE_M == D_CLEAR_M == 3.00


def test_the_horizon_matches_the_dewarp_pixel_convention() -> None:
    """`dewarp` samples pixel centres (`arange + 0.5 - crop_px/2`), so the
    optical axis lands on row `crop_px/2 - 0.5`, not `crop_px/2`. Half a pixel
    is 0.2 % of the band -- it matters only in that it must not drift.
    """
    assert horizon_row(640) == 319.5
    assert horizon_row(512) == 255.5


def test_focal_px_is_the_bridges_and_not_a_second_copy() -> None:
    assert focal_px(CROP_PX) == pytest.approx(
        (CROP_PX / 2.0) / math.tan(math.radians(CROP_FOV_DEG / 2.0))
    )


def test_band_rows_match_the_hand_computed_geometry() -> None:
    """Hand arithmetic for crop_px=640, h=1.40, band 0.20..2.20 m, d=3.00 m:

        focal          = 320 / tan(30 deg)            = 554.2563 px
        horizon        = 640/2 - 0.5                  = 319.5
        top    (2.20 m, 0.80 m ABOVE the mount)
                       = 319.5 - 554.2563*0.80/3.00   = 171.698 -> 171
        bottom (0.20 m, 1.20 m BELOW the mount)
                       = 319.5 + 554.2563*1.20/3.00   = 541.203 -> 542 (exclusive)
    """
    expected_focal = 320.0 / math.tan(math.radians(30.0))
    assert expected_focal == pytest.approx(554.2562584220407)
    top = 319.5 - expected_focal * (2.20 - 1.40) / 3.00
    bottom = 319.5 + expected_focal * (1.40 - 0.20) / 3.00
    assert (math.floor(top), math.ceil(bottom)) == (171, 542)
    assert band_rows(CROP_PX) == (171, 542)


def test_row_for_height_is_the_plans_dRow_formula() -> None:
    """`dRow = focal_px * h / d` below the horizon, for a point on the ground."""
    focal = focal_px(CROP_PX)
    for distance in (1.0, 2.5, 3.0, 7.5):
        expected = horizon_row(CROP_PX) + focal * MOUNT_HEIGHT_M / distance
        assert row_for_height(0.0, distance, CROP_PX) == pytest.approx(expected)
    # A point at mount height never leaves the horizon, at any distance.
    assert row_for_height(MOUNT_HEIGHT_M, 2.0, CROP_PX) == pytest.approx(horizon_row(CROP_PX))
    # Above the mount is above the horizon.
    assert row_for_height(2.20, 3.0, CROP_PX) < horizon_row(CROP_PX)


@pytest.mark.parametrize("crop_px", ALL_CROP_SIZES)
def test_the_band_is_always_strictly_inside_the_frame(crop_px: int) -> None:
    """The contract already rejects `top >= bottom`; this is the stronger
    property that no valid crop size can push the band off either edge, so a
    512-px rung never silently profiles the ceiling.
    """
    rows = band_rows(crop_px)
    assert rows is not None
    top, bottom = rows
    assert 0 <= top < bottom <= crop_px
    assert bottom < crop_px, "the near floor must stay out of the obstacle band"
    assert top > 0, "the far ceiling must stay out of the obstacle band"


@pytest.mark.parametrize("crop_px", ALL_CROP_SIZES)
def test_the_band_brackets_the_horizon(crop_px: int) -> None:
    """Whatever the crop size, the rows that can hold a 0.2..2.2 m obstacle at
    3 m must include the horizon itself: a 1.40 m mount stares straight at the
    middle of a person's chest.
    """
    rows = band_rows(crop_px)
    assert rows is not None
    assert rows[0] < horizon_row(crop_px) < rows[1]


@pytest.mark.parametrize("crop_px", ALL_CROP_SIZES)
def test_ground_rows_are_the_floor_the_band_threw_away(crop_px: int) -> None:
    """The anchor is fitted on exactly the rows the band filter rejected at the
    bottom -- the floor. They are disjoint from the band by construction, so a
    wall in the band can never contaminate the scale fit.
    """
    band = band_rows(crop_px)
    ground = ground_rows(crop_px)
    assert band is not None and ground is not None
    assert ground == (band[1], crop_px)
    assert ground[1] - ground[0] >= MIN_GROUND_ROWS


def test_ground_rows_map_to_plausible_metres() -> None:
    """A 60-degree crop's bottom edge looks 30 degrees down, so the nearest
    ground it can see is h/tan(30 deg) = 2.42 m; the band's bottom row is the
    ground at 3.5 m. If these ever drift, the anchor is fitting a wall.
    """
    ground = ground_rows(CROP_PX)
    assert ground is not None
    rows = np.arange(*ground)
    inverse_d = row_inverse_distance(rows, CROP_PX)
    assert np.all(inverse_d > 0.0)
    distances = 1.0 / inverse_d
    focal = 320.0 / math.tan(math.radians(30.0))
    # The first ground row is the band's exclusive bottom, 542: focal*h/(542-319.5).
    assert distances.max() == pytest.approx(focal * MOUNT_HEIGHT_M / (542 - 319.5), rel=1e-9)
    assert distances.max() == pytest.approx(3.487, abs=0.01)
    assert distances.min() == pytest.approx(MOUNT_HEIGHT_M / math.tan(math.radians(30.0)), abs=0.01)


def test_row_inverse_distance_inverts_row_for_height() -> None:
    """The two directions of the same pinhole relation, round-tripped."""
    for distance in (2.5, 3.0, 3.5):
        row = row_for_height(0.0, distance, CROP_PX)
        recovered = row_inverse_distance(np.array([row]), CROP_PX)
        assert float(recovered[0]) == pytest.approx(1.0 / distance)


def test_a_degenerate_crop_size_yields_no_band_rather_than_a_bad_one() -> None:
    """Below the contract's minimum the arithmetic still runs, and an empty or
    inverted band must come back as `None` -- absence -- never as a clamped
    rectangle that looks like evidence.
    """
    assert band_rows(0) is None
    assert band_rows(-32) is None
    assert ground_rows(0) is None
