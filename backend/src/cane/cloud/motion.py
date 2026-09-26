"""Pure geometry and range rate: box height -> range -> band, and range deltas
over *echoed* capture stamps -> ``closing_mps`` (step-07-design A6).

No clock, no I/O, no numpy: every input is passed in. ``range_m`` is computed
here and dies in ``service.py`` -- it is never returned to a caller and never
reaches the wire (the X4 path states bands, never metres).

The three ways this maths blows up are handled by rejecting the sample, not by
clamping it. A glitch clamped to the contract's +-60 m/s is indistinguishable
from a real car closing at 60 m/s, which is exactly the value that trips the
phone's urgency 3.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Final

from cane.contracts.awareness_event import CLOSING_MPS_MAX, CLOSING_MPS_MIN, RangeBand

FOV_DEG: Final = 60.0
HALF_FOV_DEG: Final = FOV_DEG / 2.0
NEAR_MAX_M: Final = 5.0
MID_MAX_M: Final = 15.0

# Below this the box is a few pixels of noise: the range estimate is worthless
# but "something is out there, far away" still is (A6.6).
MIN_BOX_H_PX: Final = 8.0
# A box this close to the top or bottom edge is vertically truncated, so its
# height tracks the crop border rather than the object (A6.6).
EDGE_EPS_PX: Final = 2.0

MIN_DT_MS: Final = 1
MAX_DT_MS: Final = 2000
EMA_ALPHA: Final = 0.5
RAW_REJECT_MPS: Final = 40.0
CLOSING_DECIMALS: Final = 3
MS_PER_S: Final = 1000.0


def focal_px(crop_px: int) -> float:
    """Pinhole focal length of a square 60-degree crop, in that crop's pixels."""
    return (crop_px / 2.0) / math.tan(math.radians(HALF_FOV_DEG))


def range_m_from_height(h_px: float, height_m: float, crop_px: int) -> float | None:
    """``f_px * H / h_px``, or ``None`` for a degenerate box.

    Invariant to ``crop_px``: ``f_px`` and ``h_px`` both scale linearly, so a
    mid-session rung step (640 -> 512) cannot jump the band.
    """
    if h_px <= 0.0 or height_m <= 0.0 or crop_px <= 0:
        return None
    return focal_px(crop_px) * height_m / h_px


def band_for_range(range_m: float) -> RangeBand:
    """near <= 5 m, mid 5-15 m, far > 15 m (contracts/README.md Sec 1)."""
    if range_m <= NEAR_MAX_M:
        return RangeBand.NEAR
    if range_m <= MID_MAX_M:
        return RangeBand.MID
    return RangeBand.FAR


@dataclass(frozen=True, slots=True)
class MotionState:
    """One track's range history. Frozen: `update_motion` returns a new one."""

    last_range_m: float
    last_t_capture_ms: int
    height_m: float
    ema_mps: float | None


def _restart(range_m: float, height_m: float, t_capture_ms: int) -> tuple[MotionState, None]:
    """Baseline replaced, EMA cleared, nothing emitted for this frame."""
    return MotionState(range_m, t_capture_ms, height_m, None), None


def _smooth(raw_mps: float, previous_ema: float | None) -> float:
    """The first accepted sample *initialises* the EMA. Blending it against 0
    would halve the first, most important reading of an approaching cyclist.
    """
    if previous_ema is None:
        return raw_mps
    return EMA_ALPHA * raw_mps + (1.0 - EMA_ALPHA) * previous_ema


def update_motion(
    state: MotionState | None, range_m: float, height_m: float, t_capture_ms: int
) -> tuple[MotionState, float | None]:
    """Fold one range sample into a track's state.

    Returns the new state and the smoothed ``closing_mps`` (positive =
    approaching), or ``None`` when this frame carries no trustworthy sample.
    """
    if state is None:
        return _restart(range_m, height_m, t_capture_ms)
    # A class flip on a live id means the series is measuring a different
    # object's height: the discontinuity is geometry, not motion.
    if height_m != state.height_m:
        return _restart(range_m, height_m, t_capture_ms)

    dt_ms = t_capture_ms - state.last_t_capture_ms
    if dt_ms < MIN_DT_MS:
        # Duplicate or out-of-order stamp: keep the baseline exactly as it was.
        return state, None
    if dt_ms > MAX_DT_MS:
        # Without the restart, a multi-second gap poisons every later delta.
        return _restart(range_m, height_m, t_capture_ms)

    raw_mps = (state.last_range_m - range_m) / (dt_ms / MS_PER_S)
    if not math.isfinite(raw_mps) or abs(raw_mps) > RAW_REJECT_MPS:
        return _restart(range_m, height_m, t_capture_ms)

    ema_mps = min(max(_smooth(raw_mps, state.ema_mps), CLOSING_MPS_MIN), CLOSING_MPS_MAX)
    ema_mps = round(ema_mps, CLOSING_DECIMALS)
    return MotionState(range_m, t_capture_ms, height_m, ema_mps), ema_mps
