"""The vertical obstacle-band filter and the ground rows (plan Sec 2.3).

The crops are rectilinear with a known focal length, the mount height is
measured once, and the camera is assumed level -- so a point at height `a`
above the ground, `d` metres away, lands

    row = horizon + focal_px * (MOUNT_HEIGHT_M - a) / d

pixels down the crop. That single relation gives both halves of this module:
the rows an obstacle 0.20..2.20 m above the ground can occupy (kept), and the
rows below them that can only be floor (thrown out of the profile and fitted
for scale instead).

`focal_px` is imported from the bridge, never re-derived: a second copy of the
same pinhole formula is how the band and the crops drift apart.
"""

from __future__ import annotations

import math

import numpy as np
from numpy.typing import NDArray

from cane.bridge.dewarp import focal_px
from cane.cloud.depth.constants import (
    BAND_REFERENCE_M,
    MIN_GROUND_ROWS,
    MOUNT_HEIGHT_M,
    OBSTACLE_BAND_M,
)


def horizon_row(crop_px: int) -> float:
    """The row the optical axis lands on.

    `dewarp` samples pixel *centres* (`arange + 0.5 - crop_px/2`), so the axis
    is at `crop_px/2 - 0.5`, not `crop_px/2`. Half a pixel is 0.2 % of the
    band; it matters only in that it must not drift from the dewarp.
    """
    return crop_px / 2.0 - 0.5


def row_for_height(
    height_m: float,
    distance_m: float,
    crop_px: int,
    mount_height_m: float = MOUNT_HEIGHT_M,
) -> float:
    """Plan Sec 2.3's `dRow`, generalised from the ground to any height.

    At `height_m == mount_height_m` the point sits on the horizon whatever the
    distance; above the mount it rises above it.
    """
    return horizon_row(crop_px) + focal_px(crop_px) * (mount_height_m - height_m) / distance_m


def band_rows(crop_px: int, mount_height_m: float = MOUNT_HEIGHT_M) -> tuple[int, int] | None:
    """The `[top, bottom)` rows that can hold an `OBSTACLE_BAND_M` obstacle at
    `BAND_REFERENCE_M`, or `None` when no such band exists in this frame.

    Half-open, like every Python row range, which is also what makes the
    contract's `top < bottom` check meaningful: `top == bottom` is an empty
    band -- no evidence -- not a one-pixel one.
    """
    if crop_px <= 0:
        return None
    low_m, high_m = OBSTACLE_BAND_M
    top = math.floor(row_for_height(high_m, BAND_REFERENCE_M, crop_px, mount_height_m))
    bottom = math.ceil(row_for_height(low_m, BAND_REFERENCE_M, crop_px, mount_height_m))
    top = min(max(top, 0), crop_px)
    bottom = min(max(bottom, 0), crop_px)
    return (top, bottom) if top < bottom else None


def ground_rows(crop_px: int, mount_height_m: float = MOUNT_HEIGHT_M) -> tuple[int, int] | None:
    """The `[top, bottom)` rows the band filter threw away at the bottom.

    They are the floor -- and therefore the scale anchor. Disjoint from the
    band by construction, so a wall inside the band can never contaminate the
    fit, and a floor covered by a crowd can never move the columns.
    """
    band = band_rows(crop_px, mount_height_m)
    if band is None:
        return None
    first = band[1]
    if crop_px - first < MIN_GROUND_ROWS:
        return None
    return first, crop_px


def row_inverse_distance(
    rows: NDArray[np.float64] | NDArray[np.int_],
    crop_px: int,
    mount_height_m: float = MOUNT_HEIGHT_M,
) -> NDArray[np.float64]:
    """`1/d` for ground points seen on `rows`, the inverse of `row_for_height`.

    Inverse distance, not distance, because that is what relative *inverse*
    depth is proportional to: the ground fit is then a straight line through
    the origin (plan Sec 2.3's "one-parameter fit").
    """
    offsets = np.asarray(rows, dtype=np.float64) - horizon_row(crop_px)
    return offsets / (focal_px(crop_px) * mount_height_m)
