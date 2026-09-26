"""Six-crop dewarp geometry (F3): our own numpy LUT, no py360convert.

The stripe tests are the geometric contract: equirect column 0 is bearing
-180, the centre column is bearing 0, and the rear crop C3 straddles the seam
without a discontinuity.
"""

from __future__ import annotations

import math

import numpy as np
import pytest

from cane.bridge.dewarp import (
    CROP_FOV_DEG,
    CROP_YAWS_DEG,
    build_remap_luts,
    dewarp_equirect,
)

EQUIRECT_W = 1440
EQUIRECT_H = 720
CROP_PX = 640
SMALL_CROP_PX = 512
STRIPE_HALF_WIDTH_COLS = 2
T_ARRIVAL_MS = 1_790_000_000_000
# One equirect column is 360/1440 = 0.25 deg; the tolerance below is ~1 crop px.
CX_TOLERANCE = 1.5 / CROP_PX


def _equirect_with_stripe(column: int) -> np.ndarray:
    image = np.zeros((EQUIRECT_H, EQUIRECT_W, 3), dtype=np.uint8)
    for offset in range(-STRIPE_HALF_WIDTH_COLS, STRIPE_HALF_WIDTH_COLS + 1):
        image[:, (column + offset) % EQUIRECT_W] = 255
    return image


def _column_of_stripe(crop: np.ndarray) -> float:
    """Intensity-weighted centroid column, normalized to 0..1."""
    weights = crop.astype(np.float64).sum(axis=(0, 2))
    assert weights.sum() > 0, "the stripe is not in this crop at all"
    columns = np.arange(crop.shape[1], dtype=np.float64) + 0.5
    return float((weights * columns).sum() / weights.sum() / crop.shape[1])


def _bearing_to_column(bearing_deg: float) -> int:
    return int(round((bearing_deg + 180.0) / 360.0 * EQUIRECT_W)) % EQUIRECT_W


def _expected_cx(bearing_deg: float, yaw_deg: float) -> float:
    """Pinhole, not linear: x = f * tan(angle from the crop axis)."""
    delta = math.radians(bearing_deg - yaw_deg)
    return 0.5 + math.tan(delta) / (2.0 * math.tan(math.radians(CROP_FOV_DEG / 2.0)))


def test_yaw_centres_are_the_device_responsibilities_table() -> None:
    assert CROP_YAWS_DEG == (0.0, 60.0, 120.0, 180.0, -120.0, -60.0)
    assert CROP_FOV_DEG == 60.0


@pytest.mark.parametrize(
    ("bearing_deg", "crop_index"),
    [(0.0, 0), (60.0, 1), (120.0, 2), (180.0, 3), (-120.0, 4), (-60.0, 5)],
)
def test_a_stripe_at_a_yaw_centre_lands_in_the_middle_of_its_crop(
    bearing_deg: float, crop_index: int
) -> None:
    image = _equirect_with_stripe(_bearing_to_column(bearing_deg))

    cropset = dewarp_equirect(image, CROP_PX, T_ARRIVAL_MS)

    assert _column_of_stripe(cropset.images[crop_index]) == pytest.approx(0.5, abs=CX_TOLERANCE)


def test_equirect_column_zero_is_bearing_minus_180_so_it_lands_in_c3() -> None:
    image = _equirect_with_stripe(0)

    cropset = dewarp_equirect(image, CROP_PX, T_ARRIVAL_MS)

    assert _column_of_stripe(cropset.images[3]) == pytest.approx(0.5, abs=CX_TOLERANCE)
    assert cropset.images[0].sum() == 0  # nothing of it leaks into the front crop


@pytest.mark.parametrize("bearing_deg", [15.0, -15.0, 25.0])
def test_off_centre_stripes_follow_the_pinhole_model(bearing_deg: float) -> None:
    image = _equirect_with_stripe(_bearing_to_column(bearing_deg))

    cropset = dewarp_equirect(image, CROP_PX, T_ARRIVAL_MS)

    assert _column_of_stripe(cropset.images[0]) == pytest.approx(
        _expected_cx(bearing_deg, 0.0), abs=CX_TOLERANCE * 2
    )


@pytest.mark.parametrize("bearing_deg", [179.0, -179.0])
def test_c3_wraps_across_the_seam(bearing_deg: float) -> None:
    image = _equirect_with_stripe(_bearing_to_column(bearing_deg))

    cropset = dewarp_equirect(image, CROP_PX, T_ARRIVAL_MS)

    assert _column_of_stripe(cropset.images[3]) == pytest.approx(
        _expected_cx(bearing_deg if bearing_deg > 0 else bearing_deg + 360.0, 180.0),
        abs=CX_TOLERANCE * 2,
    )


def test_c3_has_no_discontinuity_at_the_seam() -> None:
    """A longitude sinusoid is continuous around +-180; so must C3 be."""
    lon = (np.arange(EQUIRECT_W) + 0.5) / EQUIRECT_W * 2 * np.pi - np.pi
    row = (128 + 100 * np.sin(lon)).astype(np.uint8)
    image = np.repeat(np.repeat(row[None, :, None], EQUIRECT_H, axis=0), 3, axis=2).copy()

    rear = dewarp_equirect(image, CROP_PX, T_ARRIVAL_MS).images[3]

    middle_row = rear[CROP_PX // 2, :, 0].astype(np.int16)
    assert int(np.abs(np.diff(middle_row)).max()) <= 2


@pytest.mark.parametrize("crop_px", [CROP_PX, SMALL_CROP_PX])
def test_output_shapes_and_metadata(crop_px: int) -> None:
    image = _equirect_with_stripe(0)

    cropset = dewarp_equirect(image, crop_px, T_ARRIVAL_MS)

    assert len(cropset.images) == 6
    assert all(crop.shape == (crop_px, crop_px, 3) for crop in cropset.images)
    assert all(crop.dtype == np.uint8 for crop in cropset.images)
    assert cropset.yaws_deg == CROP_YAWS_DEG
    assert cropset.t_arrival_laptop_ms == T_ARRIVAL_MS


def test_the_lut_is_built_once_and_reused() -> None:
    build_remap_luts.cache_clear()
    image = _equirect_with_stripe(10)

    dewarp_equirect(image, CROP_PX, T_ARRIVAL_MS)
    dewarp_equirect(image, CROP_PX, T_ARRIVAL_MS + 33)
    info = build_remap_luts.cache_info()

    assert (info.misses, info.hits) == (1, 1)


def test_lut_arrays_are_float32_and_one_pair_per_crop() -> None:
    luts = build_remap_luts(EQUIRECT_W, EQUIRECT_H, CROP_PX)

    assert len(luts) == 6
    for lut in luts:
        assert lut.map_x.dtype == np.float32 and lut.map_y.dtype == np.float32
        assert lut.map_x.shape == (CROP_PX, CROP_PX)


@pytest.mark.parametrize(
    ("shape", "message"),
    [
        ((720, 720, 3), "2:1"),
        ((720, 1440), "BGR"),
    ],
)
def test_non_equirect_input_is_a_clear_error(shape: tuple[int, ...], message: str) -> None:
    with pytest.raises(ValueError, match=message):
        dewarp_equirect(np.zeros(shape, dtype=np.uint8), CROP_PX, T_ARRIVAL_MS)
