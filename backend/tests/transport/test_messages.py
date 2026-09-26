"""Envelope encode/decode tests.

Every message this module can produce must also validate against
``contracts/transport_envelope.schema.json`` -- that is the one place the
Python side's output is checked against the documentation schema (D2).
"""

import json

import pytest

from cane.transport.messages import (
    EventMessage,
    MalformedMessageError,
    Ping,
    Pong,
    Unknown,
    decode,
    encode,
    to_wire_dict,
)


def test_ping_round_trips(envelope_validator):
    msg = Ping(t1=1_790_000_000_000)
    wire = encode(msg)
    envelope_validator.validate(json.loads(wire))
    assert decode(wire) == msg


def test_pong_round_trips(envelope_validator):
    msg = Pong(t1=1_790_000_000_000, t2=1_790_000_000_012, t3=1_790_000_000_013)
    wire = encode(msg)
    envelope_validator.validate(json.loads(wire))
    assert decode(wire) == msg


def test_event_round_trips(golden_valid_event, envelope_validator):
    msg = EventMessage(event=golden_valid_event)
    wire = encode(msg)

    parsed = json.loads(wire)
    envelope_validator.validate(parsed)
    assert parsed["event"]["schema"] == 1, "event must be serialized by_alias (schema, not schema_version)"

    decoded = decode(wire)
    assert isinstance(decoded, EventMessage)
    assert decoded.event == golden_valid_event


def test_to_wire_dict_uses_schema_alias(golden_valid_event):
    wire_dict = to_wire_dict(golden_valid_event)
    assert "schema" in wire_dict
    assert "schema_version" not in wire_dict


def test_unknown_type_decodes_to_unknown():
    # `health` was the example here until step 8 defined it; anything still
    # outside ping/pong/event/health has to keep taking this path.
    decoded = decode('{"type": "battery", "battery_pct": 80}')
    assert decoded == Unknown(type="battery")


def test_reserved_location_type_also_decodes_to_unknown():
    decoded = decode('{"type": "location"}')
    assert decoded == Unknown(type="location")


@pytest.mark.parametrize(
    "text",
    [
        "not json at all {",
        "42",
        "null",
        "{}",
        '{"t1": 1000}',
        '{"type": 5}',
        '{"type": "ping"}',
        '{"type": "ping", "t1": "1000"}',
        '{"type": "pong", "t1": 1000, "t2": 1000}',
        '{"type": "event"}',
    ],
)
def test_malformed_frames_raise_malformed_message_error(text):
    with pytest.raises(MalformedMessageError):
        decode(text)


def test_event_with_invalid_contract_payload_raises_malformed_message_error():
    bad_event_frame = (
        '{"type": "event", "event": {"schema": 1, "source": "phone", '
        '"track_id": "t1", "t_capture_ms": 1790000000000, "ttl_ms": -1, '
        '"cls": "person", "confidence": 0.5, "bearing_deg": 0.0, '
        '"range_m": null, "range_band": null, "closing_mps": null}}'
    )
    with pytest.raises(MalformedMessageError):
        decode(bad_event_frame)
