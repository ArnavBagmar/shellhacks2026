"""Shared fixtures for the bridge tests (step 8 onwards).

JPEGs are encoded once per session: six 640x640 `cv2.imencode` calls are
milliseconds each, and the fallback tests post the same crops dozens of
times. Textured noise, not a flat colour -- a flat crop compresses to a few
hundred bytes and would make the decode cost unrepresentative.
"""

from __future__ import annotations

import math
from typing import Callable, Sequence

import cv2
import numpy as np
import pytest

from cane.bridge.gap_anchor import (
    NO_EVIDENCE_DEPTH,
    RELATIVE_DEPTH_SCALE_M,
    depth_for_distance_m,
)
from cane.bridge.gap_ring import (
    DEPTH_CROP_INDICES,
    RING_COLUMNS,
    GapRing,
    RingColumn,
    column_bearing_deg,
    column_camera_bearing_deg,
)
from cane.cloud.api_models import (
    COLUMNS_PER_CROP,
    CROP_COUNT,
    SCHEMA_VERSION,
    Detection,
    GapProfile,
    InferenceMeta,
    InferenceResponse,
)

T_CAPTURE_MS = 1_790_123_456_789
YAWS = (0.0, 60.0, 120.0, 180.0, -120.0, -60.0)
JPEG_QUALITY = 70
DEFAULT_CROP_PX = 640
SESSION_ID = "bridge-step8"

Jpegs = tuple[bytes, ...]


def encode_crop(crop_px: int, seed: int, quality: int = JPEG_QUALITY) -> bytes:
    rng = np.random.default_rng(seed)
    image = rng.integers(0, 255, size=(crop_px, crop_px, 3), dtype=np.uint8)
    ok, buffer = cv2.imencode(".jpg", image, [int(cv2.IMWRITE_JPEG_QUALITY), quality])
    assert ok
    return bytes(buffer)


@pytest.fixture(scope="session")
def six_jpegs() -> Callable[..., Jpegs]:
    cache: dict[tuple[int, int], Jpegs] = {}

    def build(crop_px: int = DEFAULT_CROP_PX, quality: int = JPEG_QUALITY) -> Jpegs:
        key = (crop_px, quality)
        if key not in cache:
            cache[key] = tuple(
                encode_crop(crop_px, seed, quality) for seed in range(CROP_COUNT)
            )
        return cache[key]

    return build


def make_meta(**patch: object) -> InferenceMeta:
    base: dict[str, object] = {
        "schema": SCHEMA_VERSION,
        "t_capture_ms": T_CAPTURE_MS,
        "rung": 1,
        "crop_px": DEFAULT_CROP_PX,
        "yaws_deg": YAWS,
        "session_id": SESSION_ID,
    }
    return InferenceMeta(**{**base, **patch})  # type: ignore[arg-type]


@pytest.fixture()
def meta() -> Callable[..., InferenceMeta]:
    return make_meta


# --- Step 23: scripted depth rings ------------------------------------------
# These builders live here because three test modules script the same shapes
# (a wall plane, a corridor with two gaps, one crop missing): a scripted ring
# is evidence about the world, not a fixture of any one module.

BAND_ROWS = (120, 480)
FREE_DISTANCE_M = 6.0


def make_gap_profile(
    crop_index: int,
    columns: Sequence[float | None],
    *,
    anchored: bool = True,
    ground_fit_residual: float = 0.0,
    ground_scale: float | None = RELATIVE_DEPTH_SCALE_M,
    ground_offset: float | None = 0.0,
) -> GapProfile:
    """One crop's profile. ``None`` in ``columns`` is not expressible on the
    wire, so it is written as a non-positive raw depth -- which is exactly
    how the cloud reports "no usable inverse depth here" (api_models says
    ``columns`` is of either sign).

    The default affine pair is the one ``depth_for`` writes with, so a scripted
    ring reads back at exactly the metres it was scripted in. Pass
    ``ground_scale=None, ground_offset=None`` for an **old cloud** -- a response
    from before step 27, which carries no scale at all.
    """
    values = tuple(NO_EVIDENCE_DEPTH if c is None else float(c) for c in columns)
    return GapProfile(
        crop_index=crop_index,
        columns=values,
        band_rows=BAND_ROWS,
        ground_fit_residual=ground_fit_residual,
        anchored=anchored,
        ground_scale=ground_scale,
        ground_offset=ground_offset,
    )


def depth_for(distance_m: float | None) -> float | None:
    """Raw relative depth that the anchor reads back as ``distance_m``."""
    return None if distance_m is None else depth_for_distance_m(distance_m)


def ring_bearings(delta_deg: float = 0.0) -> tuple[float, ...]:
    return tuple(
        column_bearing_deg(crop_index, column, YAWS, delta_deg)
        for crop_index in DEPTH_CROP_INDICES
        for column in range(COLUMNS_PER_CROP)
    )


def ring_camera_bearings() -> tuple[float, ...]:
    """The same columns before the mount offset -- the frame plan Sec 3.3
    ruling 1's +-90 invariant is asserted in.
    """
    return tuple(
        column_camera_bearing_deg(crop_index, column, YAWS)
        for crop_index in DEPTH_CROP_INDICES
        for column in range(COLUMNS_PER_CROP)
    )


def make_ring(distances: Sequence[float | None], delta_deg: float = 0.0) -> GapRing:
    """A ring straight from metres, bypassing assembly and smoothing.

    The steering tests are about the law, not about the seams, so they state
    the distance of every one of the 48 columns and let nothing else move.
    """
    if len(distances) != RING_COLUMNS:
        raise ValueError(f"a ring needs {RING_COLUMNS} distances, got {len(distances)}")
    crops = tuple(
        crop_index for crop_index in DEPTH_CROP_INDICES for _ in range(COLUMNS_PER_CROP)
    )
    return GapRing(
        columns=tuple(
            RingColumn(
                bearing_deg=bearing,
                camera_bearing_deg=camera_bearing,
                crop_index=crop,
                depth=depth_for(distance),
            )
            for bearing, camera_bearing, crop, distance in zip(
                ring_bearings(delta_deg),
                ring_camera_bearings(),
                crops,
                distances,
                strict=True,
            )
        )
    )


def wall_plane_distances(
    lateral_m: float,
    *,
    side: str = "left",
    free_m: float = FREE_DISTANCE_M,
    delta_deg: float = 0.0,
) -> tuple[float, ...]:
    """A flat wall running alongside the wearer at ``lateral_m``.

    A wall is not one distance: a plane at lateral offset L subtends
    ``L / sin|bearing|``, nearest at the edge of the arc and receding toward
    dead ahead. Scripting it as a single constant would make the whole left
    half equidistant, which is the one thing a real wall never is.
    """
    wall_side = -1.0 if side == "left" else 1.0
    out: list[float] = []
    for bearing in ring_bearings(delta_deg):
        sin_b = abs(math.sin(math.radians(bearing)))
        on_wall = (bearing * wall_side) > 0.0 and sin_b > 0.0
        out.append(min(free_m, lateral_m / sin_b) if on_wall else free_m)
    return tuple(out)


def make_response(
    profiles: Sequence[GapProfile],
    detections: Sequence[Detection] = (),
    t_capture_ms: int = T_CAPTURE_MS,
) -> InferenceResponse:
    return InferenceResponse(
        schema=SCHEMA_VERSION,
        t_capture_ms=t_capture_ms,
        inference_ms=3.0,
        detections=tuple(detections),
        gap_profiles=tuple(profiles),
    )
