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
from typing import get_args

from cane.bridge.dewarp import CROP_FOV_DEG
from cane.bridge.ttl import ttl_ms_for
from cane.cloud.api_models import Detection, InferenceResponse
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


def bearing_for_detection(
    detection: Detection, yaws_deg: tuple[float, ...], delta_deg: float
) -> float:
    """``yaw + (cx - 0.5) * FOV + delta``, normalized (F3).

    The linear term is the brief's mandated approximation of the pinhole
    projection (the exact inverse is ``atan(tan(FOV/2) * (2cx - 1))``); the two
    agree at the crop centre and edges and differ by at most ~1.5 degrees in
    between, which is well inside the bearing resolution the phone renders.
    """
    yaw_deg = yaws_deg[detection.crop_index]
    return normalize_bearing_deg(yaw_deg + (detection.cx - 0.5) * CROP_FOV_DEG + delta_deg)


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
        "range_band": detection.range_band.value if detection.range_band else None,
        "closing_mps": detection.closing_mps,
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
