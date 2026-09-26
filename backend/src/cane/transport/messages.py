"""The WebSocket wire envelope: three frame shapes plus JSON encode/decode.

See ``contracts/README.md`` "Transport envelope" and
``contracts/transport_envelope.schema.json`` for the documented shape.
``to_wire_dict`` is the only place an ``AwarenessEvent`` is turned into a
JSON-ready dict, so ``by_alias=True`` -- which spells the version key
``schema``, not ``schema_version`` -- can never be forgotten by a caller
(task-3-brief.md G).
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Annotated, Any

from pydantic import BaseModel, ConfigDict, Field

from cane.contracts.awareness_event import (
    T_CAPTURE_MS_MAX,
    T_CAPTURE_MS_MIN,
    AwarenessEvent,
    AwarenessEventError,
    parse_awareness_event,
)

STALE_DROP_RATE_MIN = 0.0
STALE_DROP_RATE_MAX = 1.0


class MalformedMessageError(ValueError):
    """Raised by :func:`decode` for bad JSON, a missing/malformed field, or
    an ``event`` payload that fails contract validation.
    """


@dataclass(frozen=True)
class Ping:
    t1: int


@dataclass(frozen=True)
class Pong:
    t1: int
    t2: int
    t3: int


@dataclass(frozen=True)
class EventMessage:
    event: AwarenessEvent


class PhoneHealth(BaseModel):
    """The phone's stale-drop signal (``health`` frame, phone -> laptop).

    One counter, two jobs (``docs/camera-sync-design.md`` Sec 4.4): the
    feedback manager already drops warnings that arrived too late to be
    true, and the *rate* of those drops is the cleanest trigger for the
    Sec 3.4 fallback -- no separate probe needed. The ring is 10 one-second
    buckets on the phone; this is one report of it.

    ``t_phone_ms`` is PHONE-clock time, like every other phone-produced
    stamp on this wire: the laptop converts before judging freshness.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    t_phone_ms: Annotated[int, Field(ge=T_CAPTURE_MS_MIN, le=T_CAPTURE_MS_MAX)]
    window_ms: Annotated[int, Field(ge=0)]
    received: Annotated[int, Field(ge=0)]
    stale_dropped: Annotated[int, Field(ge=0)]
    stale_drop_rate: Annotated[
        float,
        Field(ge=STALE_DROP_RATE_MIN, le=STALE_DROP_RATE_MAX, allow_inf_nan=False),
    ]
    complete_buckets: Annotated[int, Field(ge=0)]


@dataclass(frozen=True)
class Unknown:
    """A frame whose ``type`` is not one of ``ping``/``pong``/``event``/
    ``health`` (e.g. the reserved ``location``). Logged and ignored by
    callers, per D2 -- never an error.
    """

    type: str


OutboundMessage = Ping | Pong | EventMessage
InboundMessage = Ping | Pong | EventMessage | Unknown


def to_wire_dict(event: AwarenessEvent) -> dict[str, Any]:
    """The one place an ``AwarenessEvent`` becomes a JSON-ready dict --
    always ``by_alias=True`` so the wire key is ``schema``.
    """
    return event.model_dump(by_alias=True)


def encode(msg: OutboundMessage) -> str:
    """Serialize a ``Ping``, ``Pong``, or ``EventMessage`` to wire JSON."""
    if isinstance(msg, Ping):
        payload: dict[str, Any] = {"type": "ping", "t1": msg.t1}
    elif isinstance(msg, Pong):
        payload = {"type": "pong", "t1": msg.t1, "t2": msg.t2, "t3": msg.t3}
    elif isinstance(msg, EventMessage):
        payload = {"type": "event", "event": to_wire_dict(msg.event)}
    else:
        raise TypeError(f"unencodable message: {msg!r}")
    return json.dumps(payload)


def _require_int(data: dict[str, Any], key: str, *, frame_type: str) -> int:
    value = data.get(key)
    # bool is a subclass of int in Python; reject it explicitly so a stray
    # `true`/`false` on the wire is not silently accepted as 1/0.
    if key not in data or isinstance(value, bool) or not isinstance(value, int):
        raise MalformedMessageError(f"'{frame_type}' frame missing or non-integer field: {key}")
    return value


def _decode_ping(data: dict[str, Any]) -> Ping:
    return Ping(t1=_require_int(data, "t1", frame_type="ping"))


def _decode_pong(data: dict[str, Any]) -> Pong:
    return Pong(
        t1=_require_int(data, "t1", frame_type="pong"),
        t2=_require_int(data, "t2", frame_type="pong"),
        t3=_require_int(data, "t3", frame_type="pong"),
    )


def _decode_event(data: dict[str, Any]) -> EventMessage:
    if "event" not in data:
        raise MalformedMessageError("'event' frame missing 'event'")
    try:
        event = parse_awareness_event(data["event"])
    except AwarenessEventError as exc:
        raise MalformedMessageError(f"invalid event payload: {exc}") from exc
    return EventMessage(event=event)


def decode(text: str) -> InboundMessage:
    """Parse one wire frame. Raises :class:`MalformedMessageError` for bad
    JSON, a missing/wrong-typed field, or an invalid ``event`` payload. An
    unrecognised (but well-formed) ``type`` decodes to :class:`Unknown`
    rather than raising -- see D2.
    """
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise MalformedMessageError(f"malformed JSON: {exc}") from exc

    if not isinstance(data, dict):
        raise MalformedMessageError(f"frame must be a JSON object, got {type(data).__name__}")
    if "type" not in data:
        raise MalformedMessageError("frame missing 'type'")

    msg_type = data["type"]
    if msg_type == "ping":
        return _decode_ping(data)
    if msg_type == "pong":
        return _decode_pong(data)
    if msg_type == "event":
        return _decode_event(data)
    if isinstance(msg_type, str):
        return Unknown(type=msg_type)
    raise MalformedMessageError(f"'type' must be a string, got {msg_type!r}")
