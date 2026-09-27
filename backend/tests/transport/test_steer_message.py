"""The additive ``steer`` frame (laptop -> phone) of step 21: the golden
fixture both languages run, the schema that documents it, and the
forward-compatibility rule that lets an old phone survive a new laptop.

Nothing here computes a steering value -- that is step 23. This is the
shape, and only the shape (``docs/shepherd-steering-plan.md`` step 21).
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import pytest
import websockets
from jsonschema import Draft202012Validator
from pydantic import ValidationError

from cane.transport.messages import (
    STEER_TTL_MS,
    MalformedMessageError,
    Ping,
    Steer,
    Unknown,
    decode,
    encode,
)
from cane.transport.server import EventLinkServer

RECV_TIMEOUT_S = 2.0
REPO_ROOT = Path(__file__).resolve().parents[3]
FIXTURE_PATH = REPO_ROOT / "contracts" / "fixtures" / "transport_steer.golden.json"
SCHEMA_PATH = REPO_ROOT / "contracts" / "transport_envelope.schema.json"


def _cases() -> list[dict[str, Any]]:
    with FIXTURE_PATH.open("r", encoding="utf-8") as f:
        return json.load(f)["cases"]


def _valid_cases() -> list[dict[str, Any]]:
    return [case for case in _cases() if case["expect"] == "valid"]


def _frame(name: str = "steer_right_anchored") -> dict[str, Any]:
    return next(case["frame"] for case in _cases() if case["name"] == name)


async def _recv_decoded(connection: Any) -> object:
    raw = await asyncio.wait_for(connection.recv(), timeout=RECV_TIMEOUT_S)
    return decode(raw)


async def _wait_for(predicate: Any, timeout_s: float = RECV_TIMEOUT_S) -> None:
    deadline = asyncio.get_running_loop().time() + timeout_s
    while asyncio.get_running_loop().time() < deadline:
        if predicate():
            return
        await asyncio.sleep(0.01)


# --------------------------------------------------------------------------
# golden fixture: the decoder and the schema agree on every case
# --------------------------------------------------------------------------


@pytest.mark.parametrize("case", _cases(), ids=lambda c: c["name"])
def test_the_decoder_matches_the_fixture(case: dict[str, Any]) -> None:
    text = json.dumps(case["frame"])
    if case["expect"] == "valid":
        assert isinstance(decode(text), Steer)
    else:
        with pytest.raises(MalformedMessageError):
            decode(text)


@pytest.mark.parametrize("case", _cases(), ids=lambda c: c["name"])
def test_the_schema_matches_the_fixture(
    case: dict[str, Any], envelope_validator: Draft202012Validator
) -> None:
    assert envelope_validator.is_valid(case["frame"]) is (case["expect"] == "valid")


@pytest.mark.parametrize("case", _valid_cases(), ids=lambda c: c["name"])
def test_every_valid_case_re_encodes_byte_identically(case: dict[str, Any]) -> None:
    """The fixture is the wire, not a paraphrase of it: decode then encode
    must reproduce the same bytes, key order included, or the phone and the
    laptop are reading two different frames.
    """
    frame = case["frame"]
    parsed = decode(json.dumps(frame))
    assert isinstance(parsed, Steer)

    assert encode(parsed) == json.dumps(frame)


def test_the_fixture_uses_the_plans_ttl_on_every_valid_case() -> None:
    """Sec 3 pins ``STEER_TTL_MS`` at 400 ms and step 23 sends nothing else;
    a fixture drifting off it would hide a failsafe-window change.
    """
    assert STEER_TTL_MS == 400
    assert {case["frame"]["ttl_ms"] for case in _valid_cases()} == {STEER_TTL_MS}


@pytest.mark.parametrize(
    "patch",
    [
        {"steer": float("inf")},
        {"steer": float("nan")},
        {"proximity": float("inf")},
        {"gap_bearing_deg": float("nan")},
    ],
)
def test_a_non_finite_number_is_rejected(patch: dict[str, float]) -> None:
    """``json.dumps`` happily emits ``Infinity``/``NaN``; neither is JSON and
    neither is a steering command. The fixture cannot carry these cases, so
    they live here.
    """
    with pytest.raises(MalformedMessageError):
        decode(json.dumps({**_frame(), **patch}))


@pytest.mark.parametrize(
    "patch",
    [
        {"steer": True},
        {"proximity": True},
        {"ttl_ms": True},
        {"steer": "0.42"},
        {"blocked": 1},
        {"proximity_anchored": None},
        {"source": None},
    ],
)
def test_a_wrongly_typed_field_is_rejected(patch: dict[str, object]) -> None:
    """A bool is a subclass of int in Python and JSON's ``true`` must never
    slip through as 1 -- the rule ``_require_int`` already enforces for
    ``ping``.
    """
    with pytest.raises(MalformedMessageError):
        decode(json.dumps({**_frame(), **patch}))


def test_an_integer_literal_is_accepted_where_a_float_is_declared() -> None:
    """A bridge serialising ``0`` rather than ``0.0`` must not be rejected --
    the same leniency ``InferenceMeta.yaws_deg`` already grants.
    """
    frame = {**_frame(), "steer": 0, "proximity": 1, "gap_bearing_deg": 90}
    parsed = decode(json.dumps(frame))
    assert isinstance(parsed, Steer)
    assert (parsed.steer, parsed.proximity, parsed.gap_bearing_deg) == (0.0, 1.0, 90.0)


def test_a_steer_is_frozen() -> None:
    parsed = decode(json.dumps(_frame()))
    with pytest.raises(ValidationError):
        parsed.steer = 0.9  # type: ignore[misc]


def test_every_field_decodes() -> None:
    parsed = decode(json.dumps(_frame()))
    assert isinstance(parsed, Steer)
    assert parsed.t_capture_ms == 1_790_000_000_000
    assert parsed.ttl_ms == STEER_TTL_MS
    assert parsed.steer == 0.42
    assert parsed.proximity == 0.61
    assert parsed.gap_bearing_deg == 37.5
    assert parsed.blocked is False
    assert parsed.proximity_anchored is True
    assert parsed.source == "x4_cloud"


# --------------------------------------------------------------------------
# the schema
# --------------------------------------------------------------------------


def test_the_schema_documents_steer_in_its_oneof() -> None:
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))

    assert {"$ref": "#/$defs/steer"} in schema["oneOf"]
    assert schema["$defs"]["steer"]["additionalProperties"] is False


def test_the_schemas_frame_count_matches_its_oneof() -> None:
    """The description states the number of shapes in prose; the ``oneOf``
    states it in schema. Step 21 is the second time a shape was added, and
    the first one (``diag``) left the prose saying "six" -- so this pins the
    two together instead of trusting a reader to re-count.
    """
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    words = {
        1: "one", 2: "two", 3: "three", 4: "four", 5: "five",
        6: "six", 7: "seven", 8: "eight", 9: "nine", 10: "ten",
        # Step 29 added `ground`, the third shape to be added and the second
        # time this guard has earned its keep.
        11: "eleven", 12: "twelve",
    }

    assert f"{words[len(schema['oneOf'])]} WebSocket frame shapes" in schema["description"]


def test_the_schemas_steer_examples_validate_and_decode(
    envelope_validator: Draft202012Validator,
) -> None:
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))

    examples = schema["$defs"]["steer"]["examples"]
    assert examples
    for example in examples:
        envelope_validator.validate(example)
        assert isinstance(decode(json.dumps(example)), Steer)


# --------------------------------------------------------------------------
# forward compatibility: the property that lets an old phone survive a new
# laptop (plan Sec 4.1). Pinned, not introduced -- `decode` already does it.
# --------------------------------------------------------------------------


def test_an_unknown_frame_type_does_not_raise() -> None:
    assert decode('{"type": "telemetry", "cpu_pct": 41, "steer": 1.5}') == Unknown(
        type="telemetry"
    )


async def test_an_unknown_frame_type_is_counted_and_the_link_stays_up(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A phone from a newer build sending a frame this laptop has never
    heard of is a version skew, not a fault: counted, logged, connection
    untouched.
    """
    caplog.set_level("INFO", logger="cane.transport.server")
    async with EventLinkServer("127.0.0.1", 0, ping_interval_s=0.05) as srv:
        async with websockets.connect(f"ws://127.0.0.1:{srv.port}") as phone:
            await _recv_decoded(phone)
            await phone.send('{"type": "telemetry", "cpu_pct": 41}')
            await _wait_for(lambda: srv.frames_ignored == 1)
            # The link is alive: pings keep arriving after the odd frame.
            assert isinstance(await _recv_decoded(phone), Ping)

    assert srv.frames_ignored == 1
    assert any("telemetry" in record.getMessage() for record in caplog.records)
