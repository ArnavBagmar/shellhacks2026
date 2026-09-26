"""The bridge<->cloud inference contract (task-6-brief.md F4, task-7-brief.md D).

Both sides code against these models: the bridge builds the multipart request,
the cloud parses it and answers with :class:`InferenceResponse`. The cloud is
*timeless* -- it echoes ``t_capture_ms`` byte-identically and never reads a
clock -- and it never emits bearings or metres: there is deliberately no
``range_m`` field anywhere below.
"""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field

from cane.contracts.awareness_event import (
    CLOSING_MPS_MAX,
    CLOSING_MPS_MIN,
    CONFIDENCE_MAX,
    CONFIDENCE_MIN,
    T_CAPTURE_MS_MAX,
    T_CAPTURE_MS_MIN,
    TRACK_ID_MAX_LENGTH,
    TRACK_ID_MIN_LENGTH,
    ObjectClass,
    RangeBand,
)

SCHEMA_VERSION = 1
CROP_COUNT = 6
META_FIELD = "meta"
CROP_FIELD_PREFIX = "crop_"
CROP_CONTENT_TYPE = "image/jpeg"
RUNG_MIN = 1
RUNG_MAX = 4
CROP_PX_MIN = 256
CROP_PX_MAX = 1280
CROP_PX_MULTIPLE_OF = 32
SESSION_ID_PATTERN = r"^[A-Za-z0-9_.:-]{1,64}$"
NORM_MIN = 0.0
NORM_MAX = 1.0
CROP_INDEX_MIN = 0
CROP_INDEX_MAX = CROP_COUNT - 1

_STRICT = ConfigDict(extra="forbid", frozen=True, strict=True, populate_by_name=True)
# Wire numbers are JSON, so an integer literal must be accepted where a float
# is expected (yaws_deg). Strictness everywhere else stays on.
LenientFloat = Annotated[float, Field(strict=False)]


def crop_field_name(index: int) -> str:
    """Multipart field name for crop ``index`` (``crop_0`` .. ``crop_5``)."""
    if not CROP_INDEX_MIN <= index <= CROP_INDEX_MAX:
        raise ValueError(f"crop index must be 0..{CROP_INDEX_MAX}, got {index}")
    return f"{CROP_FIELD_PREFIX}{index}"


class InferenceMeta(BaseModel):
    """The JSON ``meta`` part of the multipart request.

    ``t_capture_ms`` is already PHONE-clock time: the laptop converts it
    through ``PhoneClock.capture_time_ms`` *before* sending, and the cloud
    only echoes it back.
    """

    model_config = _STRICT

    schema_version: Literal[1] = Field(alias="schema")
    t_capture_ms: Annotated[int, Field(ge=T_CAPTURE_MS_MIN, le=T_CAPTURE_MS_MAX)]
    rung: Annotated[int, Field(ge=RUNG_MIN, le=RUNG_MAX)]
    crop_px: Annotated[
        int, Field(ge=CROP_PX_MIN, le=CROP_PX_MAX, multiple_of=CROP_PX_MULTIPLE_OF)
    ]
    yaws_deg: Annotated[
        tuple[LenientFloat, ...], Field(min_length=CROP_COUNT, max_length=CROP_COUNT)
    ]
    session_id: Annotated[str, Field(pattern=SESSION_ID_PATTERN)]


class Detection(BaseModel):
    """One tracked box in one crop, in that crop's normalized coordinates."""

    model_config = _STRICT

    crop_index: Annotated[int, Field(ge=CROP_INDEX_MIN, le=CROP_INDEX_MAX)]
    cx: Annotated[float, Field(ge=NORM_MIN, le=NORM_MAX, allow_inf_nan=False)]
    cy: Annotated[float, Field(ge=NORM_MIN, le=NORM_MAX, allow_inf_nan=False)]
    w: Annotated[float, Field(ge=NORM_MIN, le=NORM_MAX, allow_inf_nan=False)]
    h: Annotated[float, Field(ge=NORM_MIN, le=NORM_MAX, allow_inf_nan=False)]
    cls: Annotated[ObjectClass, Field(strict=False)]
    confidence: Annotated[
        float, Field(ge=CONFIDENCE_MIN, le=CONFIDENCE_MAX, allow_inf_nan=False)
    ]
    track_id: Annotated[
        str, Field(min_length=TRACK_ID_MIN_LENGTH, max_length=TRACK_ID_MAX_LENGTH)
    ]
    closing_mps: Annotated[
        float | None, Field(ge=CLOSING_MPS_MIN, le=CLOSING_MPS_MAX, allow_inf_nan=False)
    ] = None
    range_band: Annotated[RangeBand | None, Field(strict=False)] = None


class InferenceResponse(BaseModel):
    """What ``POST /infer`` answers. ``t_capture_ms`` is echoed, never recomputed."""

    model_config = _STRICT

    schema_version: Literal[1] = Field(alias="schema")
    t_capture_ms: Annotated[int, Field(ge=T_CAPTURE_MS_MIN, le=T_CAPTURE_MS_MAX)]
    inference_ms: Annotated[float, Field(ge=0.0, allow_inf_nan=False)]
    detections: tuple[Detection, ...] = ()


class InferenceRequest(BaseModel):
    """``meta`` + exactly six JPEG crops, ready to be posted as multipart."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    meta: InferenceMeta
    crops: Annotated[tuple[bytes, ...], Field(min_length=CROP_COUNT, max_length=CROP_COUNT)]

    def multipart_data(self) -> dict[str, str]:
        return {META_FIELD: self.meta.model_dump_json(by_alias=True)}

    def multipart_files(self) -> list[tuple[str, tuple[str, bytes, str]]]:
        """httpx ``files=`` payload: ``(field, (filename, bytes, content-type))``."""
        return [
            (crop_field_name(i), (f"{crop_field_name(i)}.jpg", data, CROP_CONTENT_TYPE))
            for i, data in enumerate(self.crops)
        ]

    @property
    def total_bytes(self) -> int:
        return sum(len(crop) for crop in self.crops)
