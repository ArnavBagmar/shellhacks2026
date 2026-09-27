"""One inference response -> one ``ground`` frame, or honestly nothing (step 29).

The sibling of ``steer_frame``, and it obeys the same single rule: **never send
a frame that looks like a measurement when it is not.** Four refusals, all of
it that rule:

1. A response from the local detector sends nothing. The local backend is built
   with ``depth=None``, so it has not looked at the floor at all, and a frame
   from it would be a silent fake of having looked.
2. A response with no ground hazard on any profile sends nothing. There is no
   "floor is clear" frame, for the same reason ``steer`` has no neutral one --
   a clear floor and a floor nobody could read are both silence here, and the
   detector deliberately reports them as one answer.
3. A hazard under ``MIN_ANNOUNCE_CONFIDENCE`` sends nothing. The floor lives
   here and not on the phone because `camera-sync-design.md` Sec 2.3's argument
   is about what the *system* claims, and the laptop is the last place that can
   still see the residual behind the claim.
4. A hazard behind the wearer sends nothing. You do not walk backwards, and the
   depth head only runs on the forward crops anyway; the check is the same
   camera-frame invariant ``steer_frame`` asserts, for the same reason.

No clock is read. ``t_capture_ms`` is the stamp the cloud echoed, already in
phone time and D-corrected, so the ground cue ages in the same clock domain as
the events beside it.
"""

from __future__ import annotations

import logging
from typing import Final, Sequence

from cane.bridge.gap_ring import (
    COLUMN_WIDTH_DEG,
    column_bearing_deg,
    column_camera_bearing_deg,
)
from cane.bridge.steer_frame import CAMERA_BEARING_ABS_MAX_DEG
from cane.cloud.api_models import GapProfile, InferenceResponse
from cane.contracts.awareness_event import Source
from cane.transport.messages import GROUND_TTL_MS, Ground

logger = logging.getLogger("cane.bridge.ground_frame")

#: The confidence floor, and the whole of Sec 2.3's defence. `_confidence` maps
#: the detector's drop ratio onto 0..1 between 1.35x and 3.0x, so 0.35 here is
#: roughly "the floor reads twice as far away as it should". Deliberately high:
#: the argument against a curb detector was never that it would miss things, it
#: was that a wrong one teaches the wearer to ignore a right one.
MIN_ANNOUNCE_CONFIDENCE: Final = 0.35

__all__ = ["GROUND_TTL_MS", "MIN_ANNOUNCE_CONFIDENCE", "ground_from_response"]


def ground_from_response(
    response: InferenceResponse,
    yaws_deg: tuple[float, ...],
    delta_deg: float,
    source: Source,
) -> Ground | None:
    """The most confident forward ground hazard in this response, or ``None``."""
    if source is not Source.X4_CLOUD:
        return None

    best = _most_confident(response.gap_profiles, yaws_deg)
    if best is None:
        return None
    profile, hazard = best

    if hazard.confidence < MIN_ANNOUNCE_CONFIDENCE:
        return None

    return Ground(
        t_capture_ms=response.t_capture_ms,
        ttl_ms=GROUND_TTL_MS,
        bearing_deg=column_bearing_deg(
            profile.crop_index, hazard.column, yaws_deg, delta_deg
        ),
        span_deg=hazard.span * COLUMN_WIDTH_DEG,
        drop_ratio=hazard.drop_ratio,
        confidence=hazard.confidence,
        source=Source.X4_CLOUD.value,
    )


def _most_confident(profiles: Sequence[GapProfile], yaws_deg: tuple[float, ...]):
    """The worst forward hazard across the crops, or ``None``.

    One frame per tick, not one per crop: the wearer has one pair of feet, and
    the nearest thing they can fall into is the only one worth a sentence.
    """
    best = None
    for profile in profiles:
        hazard = profile.ground_hazard
        if hazard is None:
            continue
        if not _is_forward(profile.crop_index, hazard.column, yaws_deg):
            continue
        if best is None or hazard.confidence > best[1].confidence:
            best = (profile, hazard)
    return best


def _is_forward(crop_index: int, column: int, yaws_deg: tuple[float, ...]) -> bool:
    """Read in the **camera** frame, before the mount offset -- the same frame
    ``steer_frame`` asserts its arc invariant in, so a mount offset can never
    cost a frame and a wrong yaw table can never be normalised into looking
    legal."""
    try:
        camera_bearing = column_camera_bearing_deg(crop_index, column, yaws_deg)
    except ValueError:
        logger.warning("crop %d has no yaw in this run; dropping its hazard", crop_index)
        return False
    return abs(camera_bearing) <= CAMERA_BEARING_ABS_MAX_DEG
