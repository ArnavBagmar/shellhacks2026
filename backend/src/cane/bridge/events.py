"""Inference detections -> ``AwarenessEvent``.

The inference side is timeless and geometry-free: it returns boxes in crop
coordinates and echoes the capture stamp. Everything below -- bearing, TTL,
the "never state metres" rule -- is the bridge's job.

``source`` is a parameter, not a constant: step 8's health monitor can put
the *same* service behind either ``x4_cloud`` or ``x4_local``, and the phone
must be told which one answered (step-07-design H2).
"""

from __future__ import annotations

import logging
import math
from typing import get_args

from cane.bridge.dewarp import CROP_FOV_DEG, NADIR_CROP_INDEX, NADIR_FOV_DEG
from cane.bridge.ttl import ttl_ms_for
from cane.cloud.api_models import Detection, InferenceResponse
from cane.cloud.depth.constants import MOUNT_HEIGHT_M
from cane.cloud.motion import band_for_range
from cane.contracts.awareness_event import (
    AwarenessEvent,
    AwarenessEventError,
    Source,
    parse_awareness_event,
)

logger = logging.getLogger(__name__)

HALF_TURN_DEG = 180.0
FULL_TURN_DEG = 360.0


def _contract_schema_version() -> int:
    """The version the AwarenessEvent contract itself declares.

    ``AwarenessEvent.schema_version`` is a ``Literal``, so the contract is
    the single source of truth and the bridge never carries its own copy to
    drift out of date.
    """
    (version,) = get_args(AwarenessEvent.model_fields["schema_version"].annotation)
    return int(version)


SCHEMA_VERSION = _contract_schema_version()


def normalize_bearing_deg(deg: float) -> float:
    """Fold a bearing into the contract's half-open ``(-180, 180]``.

    ``atan2``-style maths produces exactly -180 for "directly behind", which
    the contract rejects; -180 must be emitted as +180 (contracts/README.md).
    """
    wrapped = ((deg + HALF_TURN_DEG) % FULL_TURN_DEG) - HALF_TURN_DEG
    return wrapped + FULL_TURN_DEG if wrapped <= -HALF_TURN_DEG else wrapped


def nadir_off_axis_deg(cx: float, cy: float) -> float:
    """Angle between the nadir and a C6 box centre at normalised ``(cx, cy)``.

    C6 is a pinhole of ``NADIR_FOV_DEG``: a point ``rho`` (in half-widths)
    from the centre is ``atan(rho * tan(FOV / 2))`` off the axis.
    """
    rho = 2.0 * math.hypot(cx - 0.5, cy - 0.5)
    return math.degrees(math.atan(rho * math.tan(math.radians(NADIR_FOV_DEG / 2.0))))


def nadir_bearing_deg(cx: float, cy: float, delta_deg: float) -> float:
    """C6 has forward at image-up and right at image-right, so a box bears
    toward its direction from the crop centre; ``delta_deg`` still rotates it.
    """
    return normalize_bearing_deg(math.degrees(math.atan2(cx - 0.5, -(cy - 0.5))) + delta_deg)


def nadir_floor_distance_m(cx: float, cy: float) -> float:
    """Horizontal distance to a floor-contact point seen at ``(cx, cy)`` in C6,
    from the shoulder height. Used only to choose a band -- the X4 path never
    emits metres. The crop's edge midpoints are 60 degrees off the nadir (2.42 m
    at 1.4 m, where the ring takes over); its corners reach 67.8 degrees, about
    3.4 m, which is still well inside the 5 m ``near`` band.
    """
    return MOUNT_HEIGHT_M * math.tan(math.radians(nadir_off_axis_deg(cx, cy)))


def bearing_for_detection(
    detection: Detection, yaws_deg: tuple[float, ...], delta_deg: float
) -> float:
    """Ring crops: ``yaw + (cx - 0.5) * FOV + delta``, normalized (F3).
    C6: the radial direction of the box from the crop centre.

    The linear term is the brief's mandated approximation of the pinhole
    projection (the exact inverse is ``atan(tan(FOV/2) * (2cx - 1))``); the two
    agree at the crop centre and edges and differ by at most ~1.5 degrees in
    between, which is well inside the bearing resolution the phone renders.
    """
    if detection.crop_index == NADIR_CROP_INDEX:
        return nadir_bearing_deg(detection.cx, detection.cy, delta_deg)
    yaw_deg = yaws_deg[detection.crop_index]
    return normalize_bearing_deg(yaw_deg + (detection.cx - 0.5) * CROP_FOV_DEG + delta_deg)


def _range_band_and_closing(detection: Detection) -> tuple[str | None, float | None]:
    """The cloud's band and closing speed come from box height against a
    horizon-level pinhole. Looking straight down that means nothing, so a C6
    detection takes its band from the floor geometry and no closing speed.
    """
    if detection.crop_index == NADIR_CROP_INDEX:
        return band_for_range(nadir_floor_distance_m(detection.cx, detection.cy)).value, None
    return (
        detection.range_band.value if detection.range_band else None,
        detection.closing_mps,
    )


def event_from_detection(
    detection: Detection,
    t_capture_ms: int,
    yaws_deg: tuple[float, ...],
    delta_deg: float,
    source: Source = Source.X4_CLOUD,
) -> AwarenessEvent | None:
    """Build one validated event, or ``None`` if the cloud gave us something
    the contract refuses. Rejected and logged -- never raised into the loop.
    """
    if detection.crop_index >= len(yaws_deg):
        logger.warning(
            "dropping detection: crop_index %d but this run has %d crops",
            detection.crop_index,
            len(yaws_deg),
        )
        return None
    range_band, closing_mps = _range_band_and_closing(detection)
    payload = {
        "schema": SCHEMA_VERSION,
        "source": source.value,
        "track_id": detection.track_id,
        "t_capture_ms": t_capture_ms,
        "ttl_ms": ttl_ms_for(detection.cls),
        "cls": detection.cls.value,
        "confidence": detection.confidence,
        "bearing_deg": bearing_for_detection(detection, yaws_deg, delta_deg),
        # The X4 path never states metres: monocular through a fisheye with no
        # usable ground plane (device-responsibilities.md).
        "range_m": None,
        "range_band": range_band,
        "closing_mps": closing_mps,
    }
    try:
        return parse_awareness_event(payload)
    except AwarenessEventError as exc:
        logger.warning(
            "rejected a %s detection (track_id=%s): %s", source.value, detection.track_id, exc
        )
        return None


def events_from_response(
    response: InferenceResponse,
    yaws_deg: tuple[float, ...],
    delta_deg: float,
    source: Source = Source.X4_CLOUD,
) -> tuple[AwarenessEvent, ...]:
    """Every detection the contract accepts, in response order."""
    events = (
        event_from_detection(detection, response.t_capture_ms, yaws_deg, delta_deg, source)
        for detection in response.detections
    )
    return tuple(event for event in events if event is not None)
