"""Synthetic depth maps for the step-22 tests -- built by hand, never by torch.

Every depth array here is *raw relative inverse depth* in Depth Anything V2's
convention: **larger means nearer**, on no fixed scale and with an arbitrary
offset (the measured run on 2026-09-26 spanned 1.935..3.011 on noise). That
convention is the one thing the cloud's ground fit assumes, so the fixtures
state it explicitly rather than leaving it to the reader.

Nothing in this module imports torch: the suite must stay runnable in `.venv`,
where torch is not installed at all (step-07-design A4).
"""

from __future__ import annotations

import math
from typing import Final

import numpy as np
from numpy.typing import NDArray

from cane.cloud.depth.constants import MOUNT_HEIGHT_M

CROP_PX: Final = 640
CROP_FOV_DEG: Final = 60.0
NEAR: Final = 3.0
FAR: Final = 1.0


def focal(crop_px: int = CROP_PX) -> float:
    """The pinhole focal length, derived here independently of `dewarp`."""
    return (crop_px / 2.0) / math.tan(math.radians(CROP_FOV_DEG / 2.0))


def flat(value: float = 2.0, crop_px: int = CROP_PX) -> NDArray[np.float32]:
    return np.full((crop_px, crop_px), value, dtype=np.float32)


def wall_on_the_left(crop_px: int = CROP_PX, near: float = NEAR, far: float = FAR) -> NDArray[np.float32]:
    """Nearest at column 0, receding monotonically to the right."""
    ramp = np.linspace(near, far, crop_px, dtype=np.float32)
    return np.broadcast_to(ramp, (crop_px, crop_px)).astype(np.float32)


def wall_on_the_right(crop_px: int = CROP_PX, near: float = NEAR, far: float = FAR) -> NDArray[np.float32]:
    return np.ascontiguousarray(wall_on_the_left(crop_px, near, far)[:, ::-1])


def affine_ground(
    crop_px: int = CROP_PX,
    scale: float = 4.0,
    offset: float = 0.0,
    base: NDArray[np.float32] | None = None,
    from_row: int | None = None,
) -> NDArray[np.float32]:
    """A ground plane as Depth Anything V2 actually reports one: ``a/d + b``.

    Step 22 measured the model being affine-invariant in disparity, with the
    offset dominating (``b`` 2.5..3.8 against ``a`` 0.9..3.7), so a fixture that
    plants only ``a`` cannot exercise the fit that step 27 ships. Adding the
    offset to the *whole* map, not just the floor, is deliberate: the model's
    zero is a property of the inference, not of the floor rows.
    """
    return (
        ideal_ground(crop_px=crop_px, scale=scale, base=base, from_row=from_row)
        + np.float32(offset)
    ).astype(np.float32)


def ideal_ground(
    crop_px: int = CROP_PX,
    scale: float = 4.0,
    base: NDArray[np.float32] | None = None,
    from_row: int | None = None,
) -> NDArray[np.float32]:
    """A perfect ground plane below the horizon: inverse depth == scale / d.

    A ground point at horizontal distance ``d`` sits ``r = focal * h / d``
    pixels below the horizon (plan Sec 2.3), so ``1/d = r / (focal * h)`` and an
    ideal one-parameter model reads ``scale * r / (focal * h)``.
    """
    depth = flat(0.0, crop_px) if base is None else base.copy()
    horizon = crop_px / 2.0 - 0.5
    rows = np.arange(crop_px, dtype=np.float64)
    inverse_d = (rows - horizon) / (focal(crop_px) * MOUNT_HEIGHT_M)
    first = horizon if from_row is None else from_row - 1
    below = rows > first
    depth[below, :] = (scale * inverse_d[below]).astype(np.float32)[:, None]
    return depth
