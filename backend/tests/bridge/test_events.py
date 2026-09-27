"""Cloud detections -> AwarenessEvent (F4): the bridge owns bearing and TTL."""

from __future__ import annotations

import importlib
import math
from typing import Literal, get_args

import pytest

from cane.bridge import events as events_module
from cane.bridge.dewarp import NADIR_CROP_INDEX, NADIR_FOV_DEG
from cane.bridge.events import (
    events_from_response,
    nadir_floor_distance_m,
    normalize_bearing_deg,
)
from cane.cloud.depth.constants import MOUNT_HEIGHT_M
from cane.cloud.motion import NEAR_MAX_M
from cane.bridge.ttl import TTL_MS_BY_CLASS, ttl_ms_for
from cane.cloud.api_models import Detection, InferenceResponse
from cane.contracts.awareness_event import (
    AwarenessEvent,
    ObjectClass,
    RangeBand,
    Source,
    parse_awareness_event,
)

# Six ring yaws, then the nadir crop C6 (forward at image-up, so 0.0).
YAWS = (0.0, 60.0, 120.0, 180.0, -120.0, -60.0, 0.0)
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


# --- The nadir crop C6 (x4-sphere-coverage PRD, milestone 1) ----------------
#
# C6 looks straight down with image-up = forward, so a box's bearing is the
# direction of its centre from the crop centre, and its distance along the
# floor follows from the mount height and the off-nadir angle. Everything in
# C6 is inside 2.42 m at a 1.4 m shoulder, so its band is always "near".


@pytest.mark.parametrize(
    ("cx", "cy", "expected"),
    [
        (0.5, 0.25, 0.0),  # above the centre: straight ahead
        (0.75, 0.5, 90.0),  # right of the centre: to the right
        (0.5, 0.75, 180.0),  # below the centre: behind
        (0.25, 0.5, -90.0),  # left of the centre: to the left
        (0.75, 0.25, 45.0),  # up-right: ahead to the right
        (0.25, 0.75, -135.0),  # down-left: behind to the left
    ],
)
def test_a_nadir_detection_bears_toward_its_direction_from_the_crop_centre(
    cx: float, cy: float, expected: float
) -> None:
    events = events_from_response(
        _response(_detection(crop_index=NADIR_CROP_INDEX, cx=cx, cy=cy, track_id="6:1")),
        yaws_deg=YAWS,
        delta_deg=0.0,
    )

    assert events[0].bearing_deg == pytest.approx(expected)


def test_delta_deg_rotates_a_nadir_bearing_too() -> None:
    events = events_from_response(
        _response(_detection(crop_index=NADIR_CROP_INDEX, cx=0.5, cy=0.25, track_id="6:1")),
        yaws_deg=YAWS,
        delta_deg=12.5,
    )

    assert events[0].bearing_deg == pytest.approx(12.5)


def test_a_nadir_detection_is_near_states_no_metres_and_has_no_closing_speed() -> None:
    """The cloud's band and closing speed come from box height against a
    horizon-level pinhole, which means nothing looking down; the bridge
    replaces both from the floor geometry and never states metres."""
    detection = _detection(
        crop_index=NADIR_CROP_INDEX, cx=0.5, cy=0.1, track_id="6:1",
        range_band="far", closing_mps=1.5,
    )

    (event,) = events_from_response(_response(detection), yaws_deg=YAWS, delta_deg=0.0)

    assert event.range_band == RangeBand.NEAR
    assert event.closing_mps is None
    assert event.range_m is None


def test_nadir_floor_distance_follows_the_120_degree_pinhole() -> None:
    half_fov = math.radians(NADIR_FOV_DEG / 2.0)
    assert nadir_floor_distance_m(0.5, 0.5) == pytest.approx(0.0)
    # the top edge of the crop is 60 degrees off the nadir: the floor at -30
    assert nadir_floor_distance_m(0.5, 0.0) == pytest.approx(MOUNT_HEIGHT_M * math.tan(half_fov))
    assert nadir_floor_distance_m(1.0, 0.5) == pytest.approx(MOUNT_HEIGHT_M * math.tan(half_fov))
    # the edge midpoints hand over to the ring at 2.42 m ...
    assert nadir_floor_distance_m(0.5, 0.0) < 2.5
    # ... but a corner is 67.8 degrees off the nadir, about 3.4 m, still "near"
    corner = nadir_floor_distance_m(1.0, 0.0)
    assert corner == pytest.approx(MOUNT_HEIGHT_M * math.tan(math.atan(math.sqrt(2.0) * math.tan(half_fov))))
    assert 3.3 < corner < 3.5 < NEAR_MAX_M


def test_a_nadir_detection_with_only_ring_yaws_is_dropped_not_misplaced(
    caplog: pytest.LogCaptureFixture,
) -> None:
    events = events_from_response(
        _response(_detection(crop_index=NADIR_CROP_INDEX)), yaws_deg=YAWS[:6], delta_deg=0.0
    )
    assert events == ()
    assert "crop_index 6" in caplog.text
