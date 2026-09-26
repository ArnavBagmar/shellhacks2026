"""The additive ``health`` frame, phone -> laptop (E4).

This is the *transport envelope*, not the frozen `AwarenessEvent` contract:
adding a fourth frame shape here is allowed, and the rule the other three
already obey applies unchanged -- a malformed frame is logged and dropped,
and the connection stays up.
"""

from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path

import pytest
import websockets

from cane.transport.messages import MalformedMessageError, PhoneHealth, Ping, decode
from cane.transport.server import EventLinkServer

RECV_TIMEOUT_S = 2.0
T_PHONE_MS = 1_790_000_000_000
SCHEMA_PATH = (
    Path(__file__).resolve().parents[3] / "contracts" / "transport_envelope.schema.json"
)


def _frame(**patch: object) -> dict[str, object]:
    base: dict[str, object] = {
        "type": "health",
        "t_phone_ms": T_PHONE_MS,
        "window_ms": 10_000,
        "received": 27,
        "stale_dropped": 7,
        "stale_drop_rate": 0.26,
        "complete_buckets": 10,
    }
    return {**base, **patch}


async def _recv_decoded(connection: object) -> object:
    raw = await asyncio.wait_for(connection.recv(), timeout=RECV_TIMEOUT_S)  # type: ignore[attr-defined]
    return decode(raw)


# --------------------------------------------------------------------------
# decode
# --------------------------------------------------------------------------


def test_a_valid_health_frame_decodes_to_the_model() -> None:
    parsed = decode(json.dumps(_frame()))

    assert isinstance(parsed, PhoneHealth)
    assert parsed.t_phone_ms == T_PHONE_MS
    assert parsed.received == 27
    assert parsed.stale_dropped == 7
    assert parsed.stale_drop_rate == pytest.approx(0.26)
    assert parsed.complete_buckets == 10


def test_the_model_is_frozen_and_forbids_unknown_keys() -> None:
    parsed = decode(json.dumps(_frame()))
    assert isinstance(parsed, PhoneHealth)

    with pytest.raises(Exception):
        parsed.received = 0  # type: ignore[misc]
    with pytest.raises(MalformedMessageError):
        decode(json.dumps(_frame(surprise=1)))


@pytest.mark.parametrize(
    "patch",
    [
        {"received": -1},
        {"stale_drop_rate": 1.5},
        {"stale_drop_rate": "many"},
        {"complete_buckets": None},
        {"t_phone_ms": 5},
    ],
)
def test_a_malformed_health_frame_is_rejected(patch: dict[str, object]) -> None:
    with pytest.raises(MalformedMessageError):
        decode(json.dumps(_frame(**patch)))


def test_a_missing_field_is_rejected() -> None:
    frame = _frame()
    del frame["received"]

    with pytest.raises(MalformedMessageError):
        decode(json.dumps(frame))


# --------------------------------------------------------------------------
# the server hook
# --------------------------------------------------------------------------


async def test_the_server_hands_a_valid_health_frame_to_on_health() -> None:
    seen: list[PhoneHealth] = []

    async with EventLinkServer(
        "127.0.0.1", 0, ping_interval_s=0.05, on_health=seen.append
    ) as srv:
        async with websockets.connect(f"ws://127.0.0.1:{srv.port}") as phone:
            await _recv_decoded(phone)
            await phone.send(json.dumps(_frame()))
            for _ in range(100):
                if seen:
                    break
                await asyncio.sleep(0.01)

    assert len(seen) == 1
    assert seen[0].stale_drop_rate == pytest.approx(0.26)


async def test_a_malformed_health_frame_is_ignored_and_the_link_stays_up(
    caplog: pytest.LogCaptureFixture,
) -> None:
    seen: list[PhoneHealth] = []

    with caplog.at_level(logging.WARNING, logger="cane.transport.server"):
        async with EventLinkServer(
            "127.0.0.1", 0, ping_interval_s=0.05, on_health=seen.append
        ) as srv:
            async with websockets.connect(f"ws://127.0.0.1:{srv.port}") as phone:
                await _recv_decoded(phone)
                await phone.send(json.dumps(_frame(stale_drop_rate=42)))
                # The connection must stay up: a second ping still arrives.
                assert isinstance(await _recv_decoded(phone), Ping)

    assert seen == []
    assert any("malformed" in r.getMessage() for r in caplog.records)


async def test_a_raising_on_health_callback_does_not_drop_the_phone() -> None:
    def explode(health: PhoneHealth) -> None:
        raise RuntimeError("the monitor blew up")

    async with EventLinkServer(
        "127.0.0.1", 0, ping_interval_s=0.05, on_health=explode
    ) as srv:
        async with websockets.connect(f"ws://127.0.0.1:{srv.port}") as phone:
            await _recv_decoded(phone)
            await phone.send(json.dumps(_frame()))
            assert isinstance(await _recv_decoded(phone), Ping)


async def test_a_health_frame_with_no_hook_is_simply_ignored() -> None:
    async with EventLinkServer("127.0.0.1", 0, ping_interval_s=0.05) as srv:
        async with websockets.connect(f"ws://127.0.0.1:{srv.port}") as phone:
            await _recv_decoded(phone)
            await phone.send(json.dumps(_frame()))
            assert isinstance(await _recv_decoded(phone), Ping)


# --------------------------------------------------------------------------
# the schema
# --------------------------------------------------------------------------


def test_the_schema_documents_health_and_its_example_validates(
    envelope_validator: object,
) -> None:
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))

    assert {"$ref": "#/$defs/health"} in schema["oneOf"]
    examples = schema["$defs"]["health"]["examples"]
    assert examples
    for example in examples:
        envelope_validator.validate(example)  # type: ignore[attr-defined]
        assert isinstance(decode(json.dumps(example)), PhoneHealth)


def test_a_health_frame_missing_a_field_fails_the_schema(
    envelope_validator: object,
) -> None:
    from jsonschema import ValidationError

    frame = _frame()
    del frame["complete_buckets"]

    with pytest.raises(ValidationError):
        envelope_validator.validate(frame)  # type: ignore[attr-defined]
