"""End to end: an `InferenceResponse` must become a valid `AwarenessEvent`.

This uses step 6's real converter (`cane.bridge.events`), not a local
re-implementation, so the proof is that the two halves actually fit -- and
that the X4 path can never state metres.
"""

from __future__ import annotations

import numpy as np
import pytest

from cane.bridge.events import events_from_response
from cane.cloud.api_models import CROP_COUNT, InferenceMeta
from cane.cloud.detector.base import RawDetection
from cane.cloud.detector.fake import ScriptedDetector
from cane.cloud.service import InferenceService
from cane.cloud.settings import CloudSettings
from cane.cloud.tracker import TrackerRegistry
from cane.contracts.awareness_event import (
    BEARING_DEG_MAX,
    BEARING_DEG_MIN_EXCLUSIVE,
    ObjectClass,
    Source,
)

T0 = 1_790_000_000_000
CROP_PX = 640
YAWS = (0.0, 60.0, 120.0, 180.0, -120.0, -60.0)
DELTA_DEG = 0.0
COCO_NAMES = ("person", "bicycle", "motorcycle", "truck", "bus", "stop sign")


def _meta(t_capture_ms: int) -> InferenceMeta:
    return InferenceMeta(
        schema=1, t_capture_ms=t_capture_ms, rung=1, crop_px=CROP_PX,
        yaws_deg=YAWS, session_id="contract",
    )


def _one_per_crop(shrink: float) -> tuple[tuple[RawDetection, ...], ...]:
    """One approaching object of a different class in every crop."""
    return tuple(
        (
            RawDetection(
                x1=200.0 + 10.0 * index,
                y1=120.0 + shrink,
                x2=320.0 + 10.0 * index,
                y2=480.0 - shrink,
                coco_name=name,
                confidence=0.82,
            ),
        )
        for index, name in enumerate(COCO_NAMES)
    )


def _service() -> InferenceService:
    settings = CloudSettings()
    frames = [_one_per_crop(30.0), _one_per_crop(10.0), _one_per_crop(0.0)]
    return InferenceService(
        detector=ScriptedDetector(frames),
        registry=TrackerRegistry(activation_threshold=settings.conf_emit),
        settings=settings,
    )


def _images() -> tuple[np.ndarray, ...]:
    return tuple(np.zeros((CROP_PX, CROP_PX, 3), dtype=np.uint8) for _ in range(CROP_COUNT))


def test_response_converts_to_a_valid_x4_cloud_awareness_event() -> None:
    service = _service()
    service.infer(_meta(T0), _images())
    response = service.infer(_meta(T0 + 333), _images())

    events = events_from_response(response, YAWS, DELTA_DEG)
    assert len(events) == len(response.detections) == CROP_COUNT
    for event in events:
        assert event.source is Source.X4_CLOUD
        assert event.schema_version == 1
        assert event.t_capture_ms == T0 + 333
        assert isinstance(event.cls, ObjectClass)
        assert BEARING_DEG_MIN_EXCLUSIVE < event.bearing_deg <= BEARING_DEG_MAX
        assert event.range_band is not None
        assert event.ttl_ms > 0
    assert any(e.closing_mps is not None and e.closing_mps > 0.0 for e in events)


def test_converted_event_always_has_range_m_none() -> None:
    """`AwarenessEvent` itself refuses a non-null `range_m` unless the source
    is the phone; this proves the cloud never even offers one.
    """
    service = _service()
    service.infer(_meta(T0), _images())
    response = service.infer(_meta(T0 + 333), _images())
    assert all(e.range_m is None for e in events_from_response(response, YAWS, DELTA_DEG))
    assert "range_m" not in response.model_dump_json(by_alias=True)


def test_every_emitted_track_id_is_unique_within_a_frame() -> None:
    """The consumer's refractory key is `(source, track_id)`: a duplicate id
    inside one frame would silently collapse two hazards into one.
    """
    service = _service()
    response = service.infer(_meta(T0), _images())
    ids = [d.track_id for d in response.detections]
    assert len(set(ids)) == len(ids) == CROP_COUNT


@pytest.mark.parametrize("rung,crop_px", [(1, 640), (3, 512)])
def test_a_rung_step_keeps_the_event_valid(rung: int, crop_px: int) -> None:
    settings = CloudSettings()
    service = InferenceService(
        detector=ScriptedDetector([_one_per_crop(20.0)]),
        registry=TrackerRegistry(activation_threshold=settings.conf_emit),
        settings=settings,
    )
    meta = InferenceMeta(
        schema=1, t_capture_ms=T0, rung=rung, crop_px=crop_px,
        yaws_deg=YAWS, session_id="rungs",
    )
    images = tuple(np.zeros((crop_px, crop_px, 3), dtype=np.uint8) for _ in range(CROP_COUNT))
    events = events_from_response(service.infer(meta, images), YAWS, DELTA_DEG)
    assert len(events) == CROP_COUNT
