"""Arrange the pipeline's six crops the way a wearer would read them.

``cane.bridge.dewarp`` emits crops in signal order -- C0 straight ahead, then
around the wearer -- which is right for the wire and wrong for a human looking
at a screen. This module owns only the display concern: which crop belongs in
which cell, and what to call it.

The yaws are imported, never restated, so this file can never disagree with
the geometry the cloud actually receives.

    +---------+---------+---------+
    | Front-L |  Front  | Front-R |
    +---------+---------+---------+
    | Rear-L  |  Rear   | Rear-R  |
    +---------+---------+---------+

The centre column is the wearer's own axis, so "what is in front of me" and
"what is behind me" sit one above the other.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from cane.bridge.dewarp import CROP_COUNT, CROP_YAWS_DEG

GRID_ROWS = 2
GRID_COLS = 3

# Reading order of the grid, by yaw. Every entry must exist in CROP_YAWS_DEG.
GRID_YAW_ORDER: tuple[float, ...] = (
    -60.0,
    0.0,
    60.0,
    -120.0,
    180.0,
    120.0,
)

YAW_LABELS: dict[float, str] = {
    0.0: "Front",
    60.0: "Front-R",
    120.0: "Rear-R",
    180.0: "Rear",
    -120.0: "Rear-L",
    -60.0: "Front-L",
}

# Yaws are whole degrees on both sides, so an exact-match tolerance is ample.
_YAW_TOLERANCE_DEG = 1e-6


def normalise_yaw(yaw_deg: float) -> float:
    """Fold a yaw into ``(-180, 180]`` so -180 and +180 name one bearing."""
    folded = (float(yaw_deg) + 180.0) % 360.0 - 180.0
    return 180.0 if folded == -180.0 else folded


def label_for_yaw(yaw_deg: float) -> str:
    """Direction name for a production crop yaw.

    Raises rather than inventing a name: an unrecognised yaw means the
    geometry changed and this table needs updating with it.
    """
    target = normalise_yaw(yaw_deg)
    for known, label in YAW_LABELS.items():
        if abs(normalise_yaw(known) - target) <= _YAW_TOLERANCE_DEG:
            return label
    raise ValueError(
        f"no direction label for yaw {yaw_deg}; known yaws are {sorted(YAW_LABELS)}"
    )


def grid_order() -> tuple[int, ...]:
    """Indices into ``CROP_YAWS_DEG``, in grid reading order."""
    order: list[int] = []
    for wanted in GRID_YAW_ORDER:
        target = normalise_yaw(wanted)
        matches = [
            index
            for index, yaw in enumerate(CROP_YAWS_DEG)
            if abs(normalise_yaw(yaw) - target) <= _YAW_TOLERANCE_DEG
        ]
        if not matches:
            raise ValueError(
                f"grid wants yaw {wanted} but the pipeline emits {CROP_YAWS_DEG}"
            )
        order.append(matches[0])
    return tuple(order)


def labels_in_grid_order() -> list[str]:
    """Direction names, in the same order as :func:`grid_order`."""
    return [label_for_yaw(CROP_YAWS_DEG[index]) for index in grid_order()]


def tile_grid(images: Sequence[Any], np: Any) -> Any:
    """Compose the crops into one ``GRID_ROWS`` x ``GRID_COLS`` canvas.

    Returns a new array; the inputs are never written through.
    """
    if len(images) != CROP_COUNT:
        raise ValueError(f"expected {CROP_COUNT} crops to tile, got {len(images)}")
    first = images[0].shape
    for index, image in enumerate(images):
        if image.shape != first:
            raise ValueError(
                f"all crops must have the same shape to tile; "
                f"crop 0 is {first} but crop {index} is {image.shape}"
            )
    order = grid_order()
    rows = [
        np.hstack([images[order[row * GRID_COLS + col]] for col in range(GRID_COLS)])
        for row in range(GRID_ROWS)
    ]
    return np.vstack(rows)
