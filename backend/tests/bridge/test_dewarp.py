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
    CROP_COUNT,
    CROP_FOV_DEG,
    CROP_YAWS_DEG,
    CROPSET_YAWS_DEG,
    NADIR_BODY_MASK_DEG,
    NADIR_CROP_INDEX,
    NADIR_FOV_DEG,
    RING_CROP_COUNT,
    build_remap_luts,
    dewarp_equirect,
    nadir_focal_px,
)
from cane.cloud import api_models

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

    assert len(cropset.images) == CROP_COUNT
    assert all(crop.shape == (crop_px, crop_px, 3) for crop in cropset.images)
    assert all(crop.dtype == np.uint8 for crop in cropset.images)
    assert cropset.yaws_deg == CROPSET_YAWS_DEG
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

    assert len(luts) == CROP_COUNT
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


# --- The nadir crop C6 (PRD x4-sphere-coverage, milestone 1) -----------------
#
# A 120-degree pinhole looking straight down, oriented so image-up is forward
# and image-right is the wearer's right. Off-nadir angle theta maps to radius
# rho = focal * tan(theta); latitude is -(90 - theta). The wearer's own body is
# blacked out inside NADIR_BODY_MASK_DEG of the nadir.

NADIR_TOLERANCE_PX = 1.5
SECTORS = 36


def _equirect_with_row_stripe(latitude_deg: float) -> np.ndarray:
    image = np.zeros((EQUIRECT_H, EQUIRECT_W, 3), dtype=np.uint8)
    row = int(round((0.5 - latitude_deg / 180.0) * EQUIRECT_H))
    for offset in range(-STRIPE_HALF_WIDTH_COLS, STRIPE_HALF_WIDTH_COLS + 1):
        image[min(max(row + offset, 0), EQUIRECT_H - 1), :] = 255
    return image


def _bright_polar(crop: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """(weights, radius_px, angle_deg) of every lit pixel, angle 0 = up, + = clockwise."""
    weights = crop.astype(np.float64).sum(axis=2)
    ys, xs = np.nonzero(weights)
    centre = crop.shape[0] / 2.0
    dx = xs + 0.5 - centre
    dy = ys + 0.5 - centre
    return weights[ys, xs], np.hypot(dx, dy), np.degrees(np.arctan2(dx, -dy)) % 360.0


def _centroid(crop: np.ndarray) -> tuple[float, float]:
    weights = crop.astype(np.float64).sum(axis=2)
    ys, xs = np.nonzero(weights)
    w = weights[ys, xs]
    assert w.sum() > 0, "nothing lit in this crop"
    return float(((xs + 0.5) * w).sum() / w.sum() / crop.shape[1]), float(
        ((ys + 0.5) * w).sum() / w.sum() / crop.shape[0]
    )


def test_the_crop_layout_matches_the_inference_contract() -> None:
    assert RING_CROP_COUNT == len(CROP_YAWS_DEG) == 6
    assert NADIR_CROP_INDEX == RING_CROP_COUNT == CROP_COUNT - 1
    assert CROP_COUNT == api_models.CROP_COUNT
    assert CROPSET_YAWS_DEG == CROP_YAWS_DEG + (0.0,)
    # The PRD's bar: the floor at 1 m from a 1.4 m shoulder is 55 degrees down.
    assert 90.0 - NADIR_BODY_MASK_DEG >= 55.0
    # The nadir crop meets the ring at its lower edge, no gap between them.
    assert NADIR_FOV_DEG / 2.0 >= 90.0 - CROP_FOV_DEG / 2.0


@pytest.mark.parametrize("latitude_deg", [-31.0, -40.0, -45.0, -50.0, -(90.0 - NADIR_BODY_MASK_DEG) + 1.0])
def test_a_row_stripe_below_the_ring_is_a_complete_ring_in_the_nadir_crop(latitude_deg: float) -> None:
    image = _equirect_with_row_stripe(latitude_deg)

    cropset = dewarp_equirect(image, CROP_PX, T_ARRIVAL_MS)

    nadir = cropset.images[NADIR_CROP_INDEX]
    weights, radius, angle = _bright_polar(nadir)
    expected = nadir_focal_px(CROP_PX) * math.tan(math.radians(90.0 + latitude_deg))
    assert float((weights * radius).sum() / weights.sum()) == pytest.approx(expected, abs=NADIR_TOLERANCE_PX)
    # every bearing: lit pixels in all 36 ten-degree sectors around the nadir
    assert len(np.unique((angle // (360.0 / SECTORS)).astype(int))) == SECTORS
    # and none of it leaks into the six ring crops, which stop at -30
    assert all(cropset.images[i].sum() == 0 for i in range(RING_CROP_COUNT))


@pytest.mark.parametrize(
    ("bearing_deg", "expected_cx", "expected_side"),
    [(0.0, 0.5, "top"), (90.0, None, "right"), (180.0, 0.5, "bottom"), (-90.0, None, "left")],
)
def test_a_column_stripe_is_a_radial_line_pointing_its_bearing_in_the_nadir_crop(
    bearing_deg: float, expected_cx: float | None, expected_side: str
) -> None:
    image = _equirect_with_stripe(_bearing_to_column(bearing_deg))

    nadir = dewarp_equirect(image, CROP_PX, T_ARRIVAL_MS).images[NADIR_CROP_INDEX]

    cx, cy = _centroid(nadir)
    if expected_side == "top":
        assert cx == pytest.approx(0.5, abs=CX_TOLERANCE) and cy < 0.4
    elif expected_side == "bottom":
        assert cx == pytest.approx(0.5, abs=CX_TOLERANCE) and cy > 0.6
    elif expected_side == "right":
        assert cy == pytest.approx(0.5, abs=CX_TOLERANCE) and cx > 0.6
    else:
        assert cy == pytest.approx(0.5, abs=CX_TOLERANCE) and cx < 0.4


def test_the_wearer_disc_is_black_and_hides_what_is_inside_it() -> None:
    inside = -(90.0 - NADIR_BODY_MASK_DEG) - 5.0  # 5 degrees inside the mask
    hidden = dewarp_equirect(_equirect_with_row_stripe(inside), CROP_PX, T_ARRIVAL_MS)
    assert all(crop.sum() == 0 for crop in hidden.images)

    white = np.full((EQUIRECT_H, EQUIRECT_W, 3), 255, dtype=np.uint8)
    nadir = dewarp_equirect(white, CROP_PX, T_ARRIVAL_MS).images[NADIR_CROP_INDEX]
    centre = CROP_PX // 2
    mask_px = nadir_focal_px(CROP_PX) * math.tan(math.radians(NADIR_BODY_MASK_DEG))
    assert int(nadir[centre, centre].sum()) == 0
    assert int(nadir[centre, centre + int(mask_px) - 2].sum()) == 0
    assert int(nadir[centre, centre + int(mask_px) + 2].sum()) == 3 * 255
    # the ring crops are untouched by the mask
    assert all(int(crop.min()) == 255 for crop in dewarp_equirect(white, CROP_PX, T_ARRIVAL_MS).images[:RING_CROP_COUNT])


def test_nadir_focal_length_is_the_120_degree_pinhole() -> None:
    assert nadir_focal_px(CROP_PX) == pytest.approx((CROP_PX / 2.0) / math.tan(math.radians(NADIR_FOV_DEG / 2.0)))
