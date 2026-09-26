"""The WebSocket wire envelope: the frame shapes plus JSON encode/decode.

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
from typing import Annotated, Any, Literal

from pydantic import (
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    ValidationError,
    model_validator,
)

from cane.contracts.awareness_event import (
    T_CAPTURE_MS_MAX,
    T_CAPTURE_MS_MIN,
    TTL_MS_MAX,
    TTL_MS_MIN,
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


QUERY_ID_PATTERN = r"^[A-Za-z0-9_-]{1,64}$"
#: ~2 MB of JPEG once decoded; keeps one query frame well inside the
#: server's WebSocket ``max_size``.
PHONE_FRAME_B64_MAX_CHARS = 2_800_000
#: Same half-open range as ``AwarenessEvent.bearing_deg``.
BEARING_MIN_EXCLUSIVE = -180.0
BEARING_MAX = 180.0

QueryKind = Literal["find", "describe_around", "describe_front"]
AnswerStatus = Literal["found", "not_found", "described", "unavailable"]
Confidence = Literal["low", "medium", "high"]


class PhoneFrame(BaseModel):
    """One rectilinear front-camera JPEG, base64. Mirroring is off on the
    phone, so image-left is the wearer's left.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    jpeg_b64: Annotated[str, Field(min_length=4, max_length=PHONE_FRAME_B64_MAX_CHARS)]
    hfov_deg: Annotated[float, Field(gt=0, lt=180, allow_inf_nan=False)]
    width: Annotated[int, Field(ge=1, le=8192)]
    height: Annotated[int, Field(ge=1, le=8192)]


class SceneQuery(BaseModel):
    """``query`` frame, phone -> laptop (step 20): a spoken question about
    the surroundings. Every key is required, nullable ones as explicit
    ``null`` -- the same rule the event contract follows.
    """

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    query_id: Annotated[str, Field(pattern=QUERY_ID_PATTERN)]
    kind: QueryKind
    target: Annotated[str | None, Field(min_length=1, max_length=60)]
    utterance: Annotated[str, Field(max_length=300)]
    t_phone_ms: int
    phone_frame: PhoneFrame | None

    @model_validator(mode="after")
    def _target_iff_find(self) -> SceneQuery:
        if (self.kind == "find") != (self.target is not None):
            raise ValueError("'target' is required for kind 'find' and must be null otherwise")
        return self


class SceneAnswer(BaseModel):
    """``answer`` frame, laptop -> phone: the one reply to a ``query``.
    ``subject``/``detail`` are noun phrases; direction travels only as
    ``bearing_deg`` so the phone speaks it in its own vocabulary.
    """

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    query_id: Annotated[str, Field(pattern=QUERY_ID_PATTERN)]
    status: AnswerStatus
    subject: Annotated[str | None, Field(min_length=1, max_length=80)]
    bearing_deg: Annotated[
        float | None, Field(gt=BEARING_MIN_EXCLUSIVE, le=BEARING_MAX, allow_inf_nan=False)
    ]
    detail: Annotated[str | None, Field(min_length=1, max_length=240)]
    confidence: Confidence | None
    latency_ms: Annotated[int, Field(ge=0)]

    @model_validator(mode="after")
    def _status_rules(self) -> SceneAnswer:
        if self.status == "found" and self.subject is None:
            raise ValueError("a 'found' answer needs a subject")
        if self.status == "described" and self.detail is None:
            raise ValueError("a 'described' answer needs a detail")
        if self.status != "found" and self.bearing_deg is not None:
            raise ValueError("only a 'found' answer carries a bearing")
        return self


DIAG_KIND_PATTERN = r"^[a-z][a-z0-9_-]{0,39}$"
DIAG_MAX_FIELDS = 24
DIAG_MAX_VALUE_CHARS = 500
DiagValue = str | int | float | bool | None


class PhoneDiagnostic(BaseModel):
    """``diag`` frame, phone -> laptop: one line of the phone's black-box
    recorder (what the mic heard, what the wake-word gate decided, what was
    said, link changes). Advisory and write-only: the laptop just records it
    so a failed walk can be read afterwards. Never audio, only text/numbers.
    """

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    t_phone_ms: int
    kind: Annotated[str, Field(pattern=DIAG_KIND_PATTERN)]
    data: Annotated[dict[str, DiagValue], Field(max_length=DIAG_MAX_FIELDS)]

    @model_validator(mode="after")
    def _short_values(self) -> PhoneDiagnostic:
        for key, value in self.data.items():
            if len(key) > 40 or (isinstance(value, str) and len(value) > DIAG_MAX_VALUE_CHARS):
                raise ValueError(f"diag field {key[:40]!r} is too long")
        return self


#: Shepherd's stage-5 command, clamped. + = right (plan Sec 0 row 7).
STEER_MIN = -1.0
STEER_MAX = 1.0
#: Shepherd's proximityFactor: 0 clear, 1 at the stopping distance.
PROXIMITY_MIN = 0.0
PROXIMITY_MAX = 1.0
#: Plan Sec 3: the most perishable row of the Sec 4.4 staleness table. Step 23
#: sends exactly this and step 24 gates staleness on it; a steer command ages
#: like a car, so it is far below ``TTL_MS_MAX``.
STEER_TTL_MS = 400

#: Closed today because depth only runs in the cloud (plan Sec 2.1). A local
#: depth path adds ``"x4_local"`` here and in the schema, and nowhere else --
#: which is the point of an enum rather than a free string.
SteerSource = Literal["x4_cloud"]


def _wire_number(value: Any) -> Any:
    """Widen a JSON integer literal to ``float`` before strict validation.

    JSON has one number type and the schema calls these fields ``number``:
    a producer that computed ``0.0`` may well emit ``0`` (Swift's
    ``JSONSerialization`` does exactly that), and rejecting it would make
    the Python decoder stricter than the schema it is documented by -- the
    same leniency ``InferenceMeta.yaws_deg`` already grants. ``bool`` is a
    subclass of ``int`` in Python and is deliberately *not* widened here, so
    ``true`` still fails the strict float below rather than arriving as 1.0.
    """
    return float(value) if type(value) is int else value


class Steer(BaseModel):
    """``steer`` frame, laptop -> phone (plan step 21): one continuous
    steering command per sensing tick.

    This is Shepherd's stage 7 -- their 12-byte BLE packet to a motor --
    carried on the WebSocket we already hold open, because steering rides
    the frame we already send. The laptop owns the geometry and the clock
    and does all of the arithmetic; the frame carries only the result, so
    the phone can act on it without knowing about crops or ground planes.

    Two fields exist to stop the phone guessing. ``proximity_anchored``
    says whether ``proximity`` came from an accepted ground-plane scale fit
    (Sec 2.3) or is only a band -- without it the phone would have to treat
    a geometric metre and a guess identically. ``blocked`` says there was no
    gap at all, which is *not* the same as ``steer == 0.0``: a clear
    corridor also steers 0.0, and the wearer needs those told apart.

    There is no "neutral" frame: in local-fallback mode the laptop stops
    sending entirely (Sec 4.2, no silent fakes) and the phone decays its own
    cue over ``FAILSAFE_DECAY_MS``.
    """

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    t_capture_ms: Annotated[int, Field(ge=T_CAPTURE_MS_MIN, le=T_CAPTURE_MS_MAX)]
    ttl_ms: Annotated[int, Field(ge=TTL_MS_MIN, le=TTL_MS_MAX)]
    steer: Annotated[
        float,
        BeforeValidator(_wire_number),
        Field(ge=STEER_MIN, le=STEER_MAX, allow_inf_nan=False),
    ]
    proximity: Annotated[
        float,
        BeforeValidator(_wire_number),
        Field(ge=PROXIMITY_MIN, le=PROXIMITY_MAX, allow_inf_nan=False),
    ]
    gap_bearing_deg: Annotated[
        float,
        BeforeValidator(_wire_number),
        Field(gt=BEARING_MIN_EXCLUSIVE, le=BEARING_MAX, allow_inf_nan=False),
    ]
    blocked: bool
    proximity_anchored: bool
    source: SteerSource


@dataclass(frozen=True)
class Unknown:
    """A frame whose ``type`` is not one of ``ping``/``pong``/``event``/
    ``health`` (e.g. the reserved ``location``). Logged and ignored by
    callers, per D2 -- never an error. This is the property that lets an old
    phone survive a new laptop (plan Sec 4.1), so it survives every frame
    shape added after it.
    """

    type: str


HEALTH_TYPE = "health"
DIAG_TYPE = "diag"
QUERY_TYPE = "query"
ANSWER_TYPE = "answer"
STEER_TYPE = "steer"

OutboundMessage = Ping | Pong | EventMessage | SceneAnswer | Steer
InboundMessage = (
    Ping
    | Pong
    | EventMessage
    | PhoneHealth
    | SceneQuery
    | SceneAnswer
    | PhoneDiagnostic
    | Steer
    | Unknown
)


def to_wire_dict(event: AwarenessEvent) -> dict[str, Any]:
    """The one place an ``AwarenessEvent`` becomes a JSON-ready dict --
    always ``by_alias=True`` so the wire key is ``schema``.
    """
    return event.model_dump(by_alias=True)


def encode(msg: OutboundMessage) -> str:
    """Serialize a ``Ping``, ``Pong``, ``EventMessage``, ``SceneAnswer`` or
    ``Steer`` to wire JSON.
    """
    if isinstance(msg, Ping):
        payload: dict[str, Any] = {"type": "ping", "t1": msg.t1}
    elif isinstance(msg, Pong):
        payload = {"type": "pong", "t1": msg.t1, "t2": msg.t2, "t3": msg.t3}
    elif isinstance(msg, EventMessage):
        payload = {"type": "event", "event": to_wire_dict(msg.event)}
    elif isinstance(msg, SceneAnswer):
        payload = {"type": ANSWER_TYPE, **msg.model_dump()}
    elif isinstance(msg, Steer):
        payload = {"type": STEER_TYPE, **msg.model_dump()}
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


def _decode_health(data: dict[str, Any]) -> PhoneHealth:
    """``type`` is the envelope's, not the payload's: it is stripped before
    validation so ``extra="forbid"`` still means "no unknown keys".
    """
    payload = {key: value for key, value in data.items() if key != "type"}
    try:
        return PhoneHealth.model_validate(payload)
    except ValidationError as exc:
        fields = sorted({".".join(str(p) for p in e["loc"]) or "health" for e in exc.errors()})
        raise MalformedMessageError(f"invalid 'health' frame fields: {fields}") from exc


def _decode_model(data: dict[str, Any], model: type[BaseModel], frame_type: str) -> Any:
    payload = {key: value for key, value in data.items() if key != "type"}
    try:
        return model.model_validate(payload)
    except ValidationError as exc:
        fields = sorted({".".join(str(p) for p in e["loc"]) or frame_type for e in exc.errors()})
        raise MalformedMessageError(f"invalid '{frame_type}' frame fields: {fields}") from exc


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
    if msg_type == HEALTH_TYPE:
        return _decode_health(data)
    if msg_type == QUERY_TYPE:
        return _decode_model(data, SceneQuery, QUERY_TYPE)
    if msg_type == DIAG_TYPE:
        return _decode_model(data, PhoneDiagnostic, DIAG_TYPE)
    if msg_type == ANSWER_TYPE:
        return _decode_model(data, SceneAnswer, ANSWER_TYPE)
    if msg_type == STEER_TYPE:
        return _decode_model(data, Steer, STEER_TYPE)
    if isinstance(msg_type, str):
        return Unknown(type=msg_type)
    raise MalformedMessageError(f"'type' must be a string, got {msg_type!r}")
