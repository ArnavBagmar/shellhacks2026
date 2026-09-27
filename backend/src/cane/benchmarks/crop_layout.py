"""Arrange the pipeline's seven crops the way a wearer would read them.

``cane.bridge.dewarp`` emits crops in signal order -- C0 straight ahead, then
around the wearer, then C6 looking straight down -- which is right for the
wire and wrong for a human looking at a screen. This module owns only the
display concern: which crop belongs in which cell, and what to call it.

The yaws are imported, never restated, so this file can never disagree with
the geometry the cloud actually receives.

    +---------+---------+---------+---------+
    | Front-L |  Front  | Front-R |  Down   |
    +---------+---------+---------+---------+
    | Rear-L  |  Rear   | Rear-R  |         |
    +---------+---------+---------+---------+

The ring's centre column is the wearer's own axis, so "what is in front of
me" and "what is behind me" sit one above the other; the nadir crop ends the
front row and the rear row's last cell stays empty.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from cane.bridge.dewarp import CROP_COUNT, CROP_YAWS_DEG, NADIR_CROP_INDEX

GRID_ROWS = 2
GRID_COLS = 4
#: The ring occupies the first three columns of each row.
RING_GRID_COLS = 3
NADIR_LABEL = "Down"

#: What each grid cell shows, in reading order: a crop index, or None for an
#: empty (black) cell. Keyed by index, not yaw, because C6's wire yaw is 0 and
#: would collide with Front.
GRID_CELLS: tuple[int | None, ...] = (
    5, 0, 1, NADIR_CROP_INDEX,  # Front-L, Front, Front-R, Down
    4, 3, 2, None,  # Rear-L, Rear, Rear-R, empty
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
    """Direction name for a production ring-crop yaw.

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


def label_for_crop(index: int) -> str:
    """Direction name for a production crop index; ``Down`` for the nadir crop."""
    if index == NADIR_CROP_INDEX:
        return NADIR_LABEL
    return label_for_yaw(CROP_YAWS_DEG[index])


def grid_order() -> tuple[int, ...]:
    """Crop indices in grid reading order, empty cells skipped.

    Raises if ``GRID_CELLS`` does not name every crop exactly once: a crop
    count change must be reflected here, never silently dropped from the grid.
    """
    order = tuple(index for index in GRID_CELLS if index is not None)
    if sorted(order) != list(range(CROP_COUNT)):
        raise ValueError(
            f"grid cells {GRID_CELLS} do not name every one of {CROP_COUNT} crops once"
        )
    return order


def labels_in_grid_order() -> list[str]:
    """Direction names, in the same order as :func:`grid_order`."""
    return [label_for_crop(index) for index in grid_order()]


def tile_grid(images: Sequence[Any], np: Any) -> Any:
    """Compose the crops into one ``GRID_ROWS`` x ``GRID_COLS`` canvas.

    Returns a new array; the inputs are never written through. Empty cells
    are black.
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
    grid_order()  # validates GRID_CELLS before any pixel is touched
    blank = np.zeros_like(images[0])
    cells = [blank if index is None else images[index] for index in GRID_CELLS]
    rows = [
        np.hstack(cells[row * GRID_COLS : (row + 1) * GRID_COLS]) for row in range(GRID_ROWS)
    ]
    return np.vstack(rows)
