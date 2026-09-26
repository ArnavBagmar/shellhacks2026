"""Cloud detections -> AwarenessEvent (F4): the bridge owns bearing and TTL."""

from __future__ import annotations

import importlib
from typing import Literal, get_args

import pytest

from cane.bridge import events as events_module
from cane.bridge.events import events_from_response, normalize_bearing_deg
from cane.bridge.ttl import TTL_MS_BY_CLASS, ttl_ms_for
from cane.cloud.api_models import Detection, InferenceResponse
from cane.contracts.awareness_event import (
    AwarenessEvent,
    ObjectClass,
    RangeBand,
    Source,
    parse_awareness_event,
)

YAWS = (0.0, 60.0, 120.0, 180.0, -120.0, -60.0)
T_CAPTURE_MS = 1_790_000_000_123


def _detection(**patch: object) -> Detection:
    base: dict[str, object] = {
        "crop_index": 0,
        "cx": 0.5,
        "cy": 0.5,
        "w": 0.1,
        "h": 0.2,
        "cls": "person",
        "confidence": 0.9,
        "track_id": "0:12",
        "closing_mps": None,
        "range_band": None,
    }
    return Detection.model_validate({**base, **patch})


def _response(*detections: Detection, t_capture_ms: int = T_CAPTURE_MS) -> InferenceResponse:
    return InferenceResponse(
        schema=1, t_capture_ms=t_capture_ms, inference_ms=5.0, detections=detections
    )


@pytest.mark.parametrize(
    ("crop_index", "cx", "expected"),
    [
        (0, 0.5, 0.0),
        (0, 1.0, 30.0),
        (0, 0.0, -30.0),
        (1, 0.5, 60.0),
        (2, 0.5, 120.0),
        (3, 0.5, 180.0),
        (3, 0.6, -174.0),
        (3, 0.4, 174.0),
        (4, 0.5, -120.0),
        (5, 0.5, -60.0),
        (4, 0.0, -150.0),
    ],
)
def test_bearing_per_crop_including_the_wrap(crop_index: int, cx: float, expected: float) -> None:
    events = events_from_response(
        _response(_detection(crop_index=crop_index, cx=cx, track_id=f"{crop_index}:1")),
        yaws_deg=YAWS,
        delta_deg=0.0,
    )

    assert events[0].bearing_deg == pytest.approx(expected)


def test_delta_deg_rotates_every_bearing() -> None:
    events = events_from_response(
        _response(_detection(crop_index=0, cx=0.5)), yaws_deg=YAWS, delta_deg=-7.5
    )
    assert events[0].bearing_deg == pytest.approx(-7.5)


def test_bearing_is_normalized_into_the_half_open_interval() -> None:
    assert normalize_bearing_deg(180.0) == 180.0
    assert normalize_bearing_deg(-180.0) == 180.0
    assert normalize_bearing_deg(186.0) == pytest.approx(-174.0)
    assert normalize_bearing_deg(-186.0) == pytest.approx(174.0)
    assert normalize_bearing_deg(540.0) == 180.0


def test_event_echoes_the_cloud_stamp_and_never_states_metres() -> None:
    events = events_from_response(
        _response(_detection(range_band="near", closing_mps=2.0)), yaws_deg=YAWS, delta_deg=0.0
    )

    event = events[0]
    assert event.source is Source.X4_CLOUD
    assert event.t_capture_ms == T_CAPTURE_MS
    assert event.range_m is None
    assert event.range_band is RangeBand.NEAR
    assert event.closing_mps == 2.0
    assert event.schema_version == 1


def test_track_id_is_used_as_given_not_prefixed() -> None:
    events = events_from_response(
        _response(_detection(track_id="3:41")), yaws_deg=YAWS, delta_deg=0.0
    )
    assert events[0].track_id == "3:41"


@pytest.mark.parametrize(
    ("cls", "ttl"),
    [
        (ObjectClass.PERSON, 1500),
        (ObjectClass.CYCLIST, 500),
        (ObjectClass.SCOOTER, 500),
        (ObjectClass.CAR, 400),
        (ObjectClass.BUS, 400),
        (ObjectClass.POLE, 8000),
        (ObjectClass.BOLLARD, 8000),
        (ObjectClass.WALL, 8000),
        (ObjectClass.PARKED_CAR, 8000),
        (ObjectClass.DOOR, 8000),
        (ObjectClass.OVERHANG, 8000),
        (ObjectClass.UNKNOWN_OBSTACLE, 1500),
    ],
)
def test_ttl_table_matches_design_section_4_4(cls: ObjectClass, ttl: int) -> None:
    assert ttl_ms_for(cls) == ttl
    assert TTL_MS_BY_CLASS[cls] == ttl


def test_every_class_in_the_enum_has_a_ttl() -> None:
    assert set(TTL_MS_BY_CLASS) == set(ObjectClass)


def test_produced_events_pass_the_shared_contract_parser() -> None:
    events = events_from_response(
        _response(
            _detection(crop_index=1, cx=0.25, cls="car", track_id="1:2", range_band="far"),
            _detection(crop_index=3, cx=0.9, cls="cyclist", track_id="3:9", closing_mps=6.0),
        ),
        yaws_deg=YAWS,
        delta_deg=0.0,
    )

    assert len(events) == 2
    for event in events:
        assert parse_awareness_event(event.model_dump(by_alias=True)) == event


def test_an_uncontractable_stamp_is_dropped_not_crashed(caplog: pytest.LogCaptureFixture) -> None:
    """Schema drift guard: a stamp the API model would have refused (built here
    with ``model_construct`` to bypass validation, as a drifted cloud would
    send it) must be rejected by the event builder, not crash the loop.
    """
    drifted = InferenceResponse.model_construct(
        schema_version=1, t_capture_ms=1, inference_ms=5.0, detections=(_detection(),)
    )

    events = events_from_response(drifted, yaws_deg=YAWS, delta_deg=0.0)

    assert events == ()
    assert "rejected" in caplog.text.lower()


def test_a_detection_pointing_at_a_crop_that_does_not_exist_is_dropped() -> None:
    events = events_from_response(
        _response(_detection(crop_index=5)), yaws_deg=YAWS[:3], delta_deg=0.0
    )
    assert events == ()


def test_the_schema_version_is_taken_from_the_contract() -> None:
    """A bridge-local copy of the version is a silent producer of rejects.

    The day the contract moves to 2, a hardcoded 1 here keeps building
    events the phone's parser refuses -- and the bridge would count them as
    rejections of the *cloud*, not of itself. So the value must come from
    the contract model, which this test proves by moving the contract.
    """
    field = AwarenessEvent.model_fields["schema_version"]
    contract_version = get_args(field.annotation)[0]
    original = field.annotation

    field.annotation = Literal[2]
    try:
        assert importlib.reload(events_module).SCHEMA_VERSION == 2
    finally:
        field.annotation = original
        importlib.reload(events_module)

    assert events_module.SCHEMA_VERSION == contract_version
