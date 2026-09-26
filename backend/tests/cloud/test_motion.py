"""Pure geometry and range-rate (A6).

Every blow-up path the architect called out has a test here, because each one
manufactures a plausible-looking closing speed rather than an obvious error:
a clamped glitch is indistinguishable from a real car on the phone.
"""

from __future__ import annotations

import math

import pytest

from cane.cloud.class_map import HEIGHT_M_BY_CLASS
from cane.cloud.motion import (
    EMA_ALPHA,
    MAX_DT_MS,
    RAW_REJECT_MPS,
    MotionState,
    band_for_range,
    focal_px,
    range_m_from_height,
    update_motion,
)
from cane.contracts.awareness_event import (
    CLOSING_MPS_MAX,
    CLOSING_MPS_MIN,
    ObjectClass,
    RangeBand,
)

PERSON_H_M = HEIGHT_M_BY_CLASS[ObjectClass.PERSON]
CYCLIST_H_M = HEIGHT_M_BY_CLASS[ObjectClass.CYCLIST]
T0 = 1_790_000_000_000


def _h_px_for_range(range_m: float, height_m: float, crop_px: int) -> float:
    return focal_px(crop_px) * height_m / range_m


def test_focal_px_matches_60_deg_fov() -> None:
    assert focal_px(640) == pytest.approx(320.0 / math.tan(math.radians(30.0)))
    assert focal_px(640) == pytest.approx(554.2562584, abs=1e-6)
    assert focal_px(512) == pytest.approx(443.4050067, abs=1e-6)


def test_band_boundaries_person_4_9_5_1_14_9_15_1() -> None:
    expected = {4.9: RangeBand.NEAR, 5.1: RangeBand.MID, 14.9: RangeBand.MID, 15.1: RangeBand.FAR}
    for target_m, band in expected.items():
        h_px = _h_px_for_range(target_m, PERSON_H_M, 640)
        estimated = range_m_from_height(h_px, PERSON_H_M, 640)
        assert estimated is not None
        assert estimated == pytest.approx(target_m, rel=1e-9)
        assert band_for_range(estimated) is band


def test_band_is_near_at_exactly_5_and_mid_at_exactly_15() -> None:
    """contracts/README.md: near <= 5 m, mid 5-15 m, far > 15 m."""
    assert band_for_range(5.0) is RangeBand.NEAR
    assert band_for_range(15.0) is RangeBand.MID
    assert band_for_range(0.0) is RangeBand.NEAR
    assert band_for_range(15.000001) is RangeBand.FAR


def test_range_is_invariant_to_crop_px() -> None:
    """A rung step 640 -> 512 must not jump the band mid-track."""
    fraction = 0.3
    at_640 = range_m_from_height(fraction * 640, PERSON_H_M, 640)
    at_512 = range_m_from_height(fraction * 512, PERSON_H_M, 512)
    assert at_640 is not None and at_512 is not None
    assert at_640 == pytest.approx(at_512, rel=1e-12)
    assert band_for_range(at_640) is band_for_range(at_512)


def test_range_from_a_non_positive_height_is_none() -> None:
    assert range_m_from_height(0.0, PERSON_H_M, 640) is None
    assert range_m_from_height(-3.0, PERSON_H_M, 640) is None


def test_first_sample_returns_none() -> None:
    state, closing = update_motion(None, 20.0, PERSON_H_M, T0)
    assert closing is None
    assert state.last_range_m == 20.0
    assert state.last_t_capture_ms == T0
    assert state.ema_mps is None


def test_closing_6_7_mps_over_three_frames_within_10_percent() -> None:
    """A cyclist at 15 mph, sampled at rung 1's 3 fps."""
    dt_ms = 333
    speed = 6.7
    state, closing = update_motion(None, 20.0, CYCLIST_H_M, T0)
    assert closing is None
    seen: list[float] = []
    range_m = 20.0
    for frame in range(1, 3):
        range_m -= speed * dt_ms / 1000.0
        state, closing = update_motion(state, range_m, CYCLIST_H_M, T0 + frame * dt_ms)
        assert closing is not None
        seen.append(closing)
    assert all(abs(c - speed) <= 0.1 * speed for c in seen)


def test_receding_is_negative() -> None:
    state, _ = update_motion(None, 10.0, PERSON_H_M, T0)
    _, closing = update_motion(state, 12.0, PERSON_H_M, T0 + 500)
    assert closing is not None and closing < 0.0
    assert closing == pytest.approx(-4.0)


def test_dt_zero_or_negative_returns_none_and_keeps_baseline() -> None:
    """An out-of-order or duplicate stamp must never rewind the baseline."""
    first, _ = update_motion(None, 20.0, PERSON_H_M, T0)
    for stamp in (T0, T0 - 200):
        state, closing = update_motion(first, 5.0, PERSON_H_M, stamp)
        assert closing is None
        assert state == first


def test_dt_over_2000ms_resets_baseline() -> None:
    first, _ = update_motion(None, 20.0, PERSON_H_M, T0)
    state, closing = update_motion(first, 18.0, PERSON_H_M, T0 + MAX_DT_MS + 1)
    assert closing is None
    assert state.last_range_m == 18.0
    assert state.last_t_capture_ms == T0 + MAX_DT_MS + 1
    assert state.ema_mps is None


def test_class_height_change_resets_baseline() -> None:
    """person -> cyclist on a live id: the range series is discontinuous."""
    state, _ = update_motion(None, 20.0, PERSON_H_M, T0)
    state, closing = update_motion(state, 19.0, PERSON_H_M, T0 + 333)
    assert closing is not None
    state, closing = update_motion(state, 20.1, CYCLIST_H_M, T0 + 666)
    assert closing is None
    assert state.height_m == CYCLIST_H_M
    assert state.ema_mps is None


def test_raw_over_40_mps_is_rejected_not_clamped() -> None:
    """Reject, do not clamp: a clamped 60 m/s scores urgency 3 on the phone."""
    state, _ = update_motion(None, 50.0, PERSON_H_M, T0)
    state, closing = update_motion(state, 20.0, PERSON_H_M, T0 + 500)  # raw = 60 m/s
    assert closing is None
    assert state.ema_mps is None
    assert state.last_range_m == 20.0


def test_a_raw_just_inside_the_reject_limit_is_kept() -> None:
    state, _ = update_motion(None, 20.0, PERSON_H_M, T0)
    _, closing = update_motion(state, 20.0 - (RAW_REJECT_MPS - 1.0), PERSON_H_M, T0 + 1000)
    assert closing == pytest.approx(RAW_REJECT_MPS - 1.0)


def test_ema_halves_a_step_change() -> None:
    """First accepted sample initialises the EMA; it never blends against 0."""
    state, _ = update_motion(None, 20.0, PERSON_H_M, T0)
    state, first = update_motion(state, 15.0, PERSON_H_M, T0 + 500)
    assert first == pytest.approx(10.0)  # not 5.0
    _, second = update_motion(state, 15.0, PERSON_H_M, T0 + 1000)
    assert second == pytest.approx(EMA_ALPHA * 0.0 + (1.0 - EMA_ALPHA) * 10.0)


def test_result_is_within_contract_bounds() -> None:
    state: MotionState | None = None
    stamp = T0
    for step, dt_ms in enumerate((1, 40, 333, 500, 1999, 2001, 333, 333)):
        stamp += dt_ms
        range_m = 20.0 - 2.3 * (step % 5)
        state, closing = update_motion(state, max(range_m, 0.2), PERSON_H_M, stamp)
        assert closing is None or CLOSING_MPS_MIN <= closing <= CLOSING_MPS_MAX
        assert math.isfinite(state.last_range_m)


def test_motion_state_is_frozen() -> None:
    state, _ = update_motion(None, 20.0, PERSON_H_M, T0)
    with pytest.raises((AttributeError, TypeError)):
        state.last_range_m = 1.0  # type: ignore[misc]
