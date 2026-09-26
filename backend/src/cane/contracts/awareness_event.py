"""The AwarenessEvent contract: a frozen, strictly-validated Pydantic model
mirroring ``contracts/awareness_event.schema.json``.

Every numeric bound below is authored once, in the schema; this module's
constants are asserted equal to it by ``tests/contracts/test_enum_drift.py``.
Callers should not construct ``AwarenessEvent`` directly from untrusted
input -- use :func:`parse_awareness_event`, which never raises anything
except :class:`AwarenessEventError`: rejected, not crashed.
"""

from __future__ import annotations

import json
from enum import StrEnum
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

# Numeric bounds -- read from contracts/awareness_event.schema.json; see
# test_enum_drift.py for the equality assertions that keep them in sync.
TRACK_ID_MIN_LENGTH = 1
TRACK_ID_MAX_LENGTH = 64
T_CAPTURE_MS_MIN = 1_600_000_000_000
T_CAPTURE_MS_MAX = 4_000_000_000_000
TTL_MS_MIN = 1
TTL_MS_MAX = 15_000
CONFIDENCE_MIN = 0.0
CONFIDENCE_MAX = 1.0
BEARING_DEG_MIN_EXCLUSIVE = -180.0
BEARING_DEG_MAX = 180.0
RANGE_M_MIN = 0.0
RANGE_M_MAX = 50.0
CLOSING_MPS_MIN = -60.0
CLOSING_MPS_MAX = 60.0


class Source(StrEnum):
    """Producer of an AwarenessEvent."""

    PHONE = "phone"
    X4_CLOUD = "x4_cloud"
    X4_LOCAL = "x4_local"


class ObjectClass(StrEnum):
    """The closed 12-value class enum. A detector class outside these 12
    must be mapped by the producer (see contracts/README.md), never emitted
    as a new string.
    """

    PERSON = "person"
    CYCLIST = "cyclist"
    SCOOTER = "scooter"
    CAR = "car"
    BUS = "bus"
    POLE = "pole"
    BOLLARD = "bollard"
    WALL = "wall"
    PARKED_CAR = "parked_car"
    DOOR = "door"
    OVERHANG = "overhang"
    UNKNOWN_OBSTACLE = "unknown_obstacle"


class RangeBand(StrEnum):
    """near <= 5 m, mid 5-15 m, far > 15 m (contracts/README.md)."""

    NEAR = "near"
    MID = "mid"
    FAR = "far"


class AwarenessEventError(ValueError):
    """Raised for any rejected AwarenessEvent payload: malformed JSON, a
    non-finite numeric literal, or a schema/model validation failure. The
    one exception type callers need to catch.
    """


class AwarenessEvent(BaseModel):
    """Eleven required fields, all three lanes code against this. Frozen:
    never mutated after construction.
    """

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    # Enum fields are validated with strict=False: the wire format is a
    # plain JSON string and must convert to the enum member by value. Model-
    # level strict=True stays on for every other field, which is what makes
    # {"ttl_ms": "500"} rejected (A5).
    schema_version: Literal[1] = Field(alias="schema")
    source: Annotated[Source, Field(strict=False)]
    track_id: Annotated[
        str, Field(min_length=TRACK_ID_MIN_LENGTH, max_length=TRACK_ID_MAX_LENGTH)
    ]
    t_capture_ms: Annotated[int, Field(ge=T_CAPTURE_MS_MIN, le=T_CAPTURE_MS_MAX)]
    ttl_ms: Annotated[int, Field(ge=TTL_MS_MIN, le=TTL_MS_MAX)]
    cls: Annotated[ObjectClass, Field(strict=False)]
    confidence: Annotated[
        float, Field(ge=CONFIDENCE_MIN, le=CONFIDENCE_MAX, allow_inf_nan=False)
    ]
    bearing_deg: Annotated[
        float, Field(gt=BEARING_DEG_MIN_EXCLUSIVE, le=BEARING_DEG_MAX, allow_inf_nan=False)
    ]
    range_m: Annotated[
        float | None, Field(ge=RANGE_M_MIN, le=RANGE_M_MAX, allow_inf_nan=False)
    ]
    range_band: Annotated[RangeBand | None, Field(strict=False)]
    closing_mps: Annotated[
        float | None, Field(ge=CLOSING_MPS_MIN, le=CLOSING_MPS_MAX, allow_inf_nan=False)
    ]

    @model_validator(mode="after")
    def _check_x4_never_states_metres(self) -> AwarenessEvent:
        if self.source is not Source.PHONE and self.range_m is not None:
            raise ValueError("range_m must be null unless source is phone")
        return self


def _reject_non_finite_literal(token: str) -> float:
    raise AwarenessEventError(f"non-finite numeric literal in payload: {token}")


def parse_awareness_event(payload: bytes | str | dict[str, Any]) -> AwarenessEvent:
    """Parse and validate an AwarenessEvent. Rejected, never crashed: the
    only exception this raises is :class:`AwarenessEventError`, wrapping
    ``json.JSONDecodeError`` and ``pydantic.ValidationError``.
    """
    if isinstance(payload, (bytes, str)):
        try:
            data: Any = json.loads(payload, parse_constant=_reject_non_finite_literal)
        except json.JSONDecodeError as exc:
            raise AwarenessEventError(f"malformed JSON: {exc}") from exc
    else:
        data = payload

    try:
        return AwarenessEvent.model_validate(data)
    except ValidationError as exc:
        raise AwarenessEventError(str(exc)) from exc
