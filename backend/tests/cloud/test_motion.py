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
    CLOSING_DEADBAND_MPS,
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


def _drive(speeds_mps: list[float], dt_ms: int = 333, start_m: float = 20.0) -> list[float | None]:
    """Report one closing value per raw speed, the baseline frame excluded.

    Positive = approaching, so the range shrinks by `speed * dt`.
    """
    state, _ = update_motion(None, start_m, PERSON_H_M, T0)
    range_m, stamp, reported = start_m, T0, []
    for speed in speeds_mps:
        range_m -= speed * dt_ms / 1000.0
        stamp += dt_ms
        state, closing = update_motion(state, range_m, PERSON_H_M, stamp)
        reported.append(closing)
    return reported


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
    """A cyclist at 15 mph, sampled at rung 1's 3 fps.

    Frame 1 is the baseline (`None`), frame 2 is the first raw sample and the
    sign-consistency gate has nothing to compare it with (`0.0`), frame 3 is
    the first *reportable* one -- and it must be the real speed, not a
    smoothed-down fraction of it. One frame of latency, 333 ms, is the price
    of not announcing a phantom cyclist to a blind walker.
    """
    dt_ms = 333
    speed = 6.7
    state, closing = update_motion(None, 20.0, CYCLIST_H_M, T0)
    assert closing is None
    seen: list[float] = []
    range_m = 20.0
    for frame in range(1, 4):
        range_m -= speed * dt_ms / 1000.0
        state, closing = update_motion(state, range_m, CYCLIST_H_M, T0 + frame * dt_ms)
        assert closing is not None
        seen.append(closing)
    assert seen[0] == 0.0
    assert all(abs(c - speed) <= 0.1 * speed for c in seen[1:])


def test_receding_consistently_is_negative() -> None:
    """Two consistent -2 m/s samples: the gate passes and the sign survives."""
    reported = _drive([-2.0, -2.0])
    assert reported[0] == 0.0
    assert reported[1] is not None and reported[1] == pytest.approx(-2.0)


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
    fast = RAW_REJECT_MPS - 1.0
    reported = _drive([fast, fast], dt_ms=1000, start_m=200.0)
    assert reported[1] == pytest.approx(fast)


def test_ema_halves_a_step_change_and_keeps_running_behind_the_gate() -> None:
    """First accepted sample initialises the EMA; it never blends against 0.

    The EMA is asserted on the *state*, because the sign-consistency gate
    decides separately what is reported -- and it must not be able to stall
    the filter, or a real approach after a jittery stretch would start from
    a stale value.
    """
    state, _ = update_motion(None, 20.0, PERSON_H_M, T0)
    state, first = update_motion(state, 15.0, PERSON_H_M, T0 + 500)
    assert state.ema_mps == pytest.approx(10.0)  # not 5.0
    assert first == 0.0  # one sample only: nothing to be consistent with
    state, second = update_motion(state, 15.0, PERSON_H_M, T0 + 1000)
    assert state.ema_mps == pytest.approx(EMA_ALPHA * 0.0 + (1.0 - EMA_ALPHA) * 10.0)
    assert second == 0.0  # the second raw is 0 m/s: inside the deadband


def test_result_is_within_contract_bounds() -> None:
    state: MotionState | None = None
    stamp = T0
    for step, dt_ms in enumerate((1, 40, 333, 500, 1999, 2001, 333, 333)):
        stamp += dt_ms
        range_m = 20.0 - 2.3 * (step % 5)
        state, closing = update_motion(state, max(range_m, 0.2), PERSON_H_M, stamp)
        assert closing is None or CLOSING_MPS_MIN <= closing <= CLOSING_MPS_MAX
        assert math.isfinite(state.last_range_m)


def test_alternating_jitter_on_a_stationary_target_reports_zero() -> None:
    """The live run: a *seated* person whose box height flickers produced
    +-1.3 m/s of closing speed frame after frame. Box-height noise alternates
    in sign; a real approach does not.
    """
    reported = _drive([1.3, -1.3, 1.3, -1.3, 1.3])
    assert reported == [0.0] * 5


def test_a_consistent_slow_drift_stays_inside_the_deadband() -> None:
    """0.3 m/s is a person shifting their weight, not an approach."""
    drift = CLOSING_DEADBAND_MPS - 0.2
    assert _drive([drift] * 4) == [0.0] * 4


def test_a_single_spike_is_never_reported() -> None:
    """One bad box in an otherwise quiet series must not reach the phone:
    5 m/s at 3 m is urgency 3, and the walker would flinch at nothing.
    """
    quiet = 0.1
    reported = _drive([quiet, quiet, 5.0, quiet, quiet])
    assert reported == [0.0] * 5


def test_two_consistent_samples_past_the_deadband_are_reported() -> None:
    """The gate must not swallow the signal it exists to protect."""
    walking = 1.4
    reported = _drive([walking, walking, walking])
    assert reported[0] == 0.0
    assert all(c == pytest.approx(walking) for c in reported[1:])


def test_a_sign_flip_after_a_real_approach_reports_zero_not_the_flip() -> None:
    reported = _drive([2.0, 2.0, -2.0, 2.0])
    assert reported[1] == pytest.approx(2.0)
    assert reported[2] == 0.0 and reported[3] == 0.0


def test_motion_state_is_frozen() -> None:
    state, _ = update_motion(None, 20.0, PERSON_H_M, T0)
    with pytest.raises((AttributeError, TypeError)):
        state.last_range_m = 1.0  # type: ignore[misc]
