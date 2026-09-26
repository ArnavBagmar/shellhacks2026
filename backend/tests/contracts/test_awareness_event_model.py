"""Model-only behaviour pinned by amendments A2 and A5 that the golden
fixture deliberately cannot exercise (NaN/Infinity are not valid JSON and
would make the fixture file itself unparseable on the Swift side).
"""

import pytest
from pydantic import ValidationError

from cane.contracts.awareness_event import AwarenessEvent, AwarenessEventError, parse_awareness_event

WIRE_KEYS = {
    "schema",
    "source",
    "track_id",
    "t_capture_ms",
    "ttl_ms",
    "cls",
    "confidence",
    "bearing_deg",
    "range_m",
    "range_band",
    "closing_mps",
}

VALID_PAYLOAD = {
    "schema": 1,
    "source": "phone",
    "track_id": "phone-001",
    "t_capture_ms": 1790000000000,
    "ttl_ms": 1500,
    "cls": "person",
    "confidence": 0.9,
    "bearing_deg": 12.0,
    "range_m": 3.5,
    "range_band": "near",
    "closing_mps": None,
}


def test_round_trip_serializes_exactly_11_wire_keys():
    event = parse_awareness_event(VALID_PAYLOAD)
    dumped = event.model_dump(by_alias=True)
    assert set(dumped.keys()) == WIRE_KEYS
    assert dumped["schema"] == 1


def test_round_trip_reparses_to_an_equal_event():
    event = parse_awareness_event(VALID_PAYLOAD)
    dumped = event.model_dump(by_alias=True)
    reloaded = parse_awareness_event(dumped)
    assert reloaded == event


def test_serialization_never_drops_null_keys():
    event = parse_awareness_event(VALID_PAYLOAD)
    dumped = event.model_dump(by_alias=True)
    assert "closing_mps" in dumped
    assert dumped["closing_mps"] is None


def test_model_is_frozen():
    event = parse_awareness_event(VALID_PAYLOAD)
    with pytest.raises(ValidationError):
        event.track_id = "changed"


def test_wire_key_must_be_spelled_schema_not_schema_version():
    payload = dict(VALID_PAYLOAD)
    del payload["schema"]
    payload["schema_version"] = 1
    with pytest.raises(AwarenessEventError):
        parse_awareness_event(payload)


def test_strict_typing_rejects_numeric_string_for_ttl_ms():
    payload = dict(VALID_PAYLOAD)
    payload["ttl_ms"] = "1500"
    with pytest.raises(AwarenessEventError):
        parse_awareness_event(payload)


@pytest.mark.parametrize("literal", ["NaN", "Infinity", "-Infinity"])
def test_non_finite_literals_are_rejected_not_crashed(literal):
    payload_text = (
        '{"schema": 1, "source": "phone", "track_id": "t1", '
        '"t_capture_ms": 1790000000000, "ttl_ms": 1500, "cls": "person", '
        f'"confidence": {literal}, "bearing_deg": 12.0, "range_m": 3.5, '
        '"range_band": "near", "closing_mps": null}'
    )
    with pytest.raises(AwarenessEventError):
        parse_awareness_event(payload_text)


def test_parse_awareness_event_raises_only_awareness_event_error_on_bad_json():
    with pytest.raises(AwarenessEventError):
        parse_awareness_event("{not json")


def test_awareness_event_error_is_a_value_error():
    assert issubclass(AwarenessEventError, ValueError)


def test_x4_never_states_metres_rejected_as_awareness_event_error():
    payload = dict(VALID_PAYLOAD)
    payload["source"] = "x4_cloud"
    payload["range_band"] = "near"
    with pytest.raises(AwarenessEventError):
        parse_awareness_event(payload)
