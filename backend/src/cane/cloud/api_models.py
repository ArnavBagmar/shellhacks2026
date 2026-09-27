"""The bridge<->cloud inference contract (task-6-brief.md F4, task-7-brief.md D).

Both sides code against these models: the bridge builds the multipart request,
the cloud parses it and answers with :class:`InferenceResponse`. The cloud is
*timeless* -- it echoes ``t_capture_ms`` byte-identically and never reads a
clock -- and it never emits bearings or metres: there is deliberately no
``range_m`` field anywhere below.
"""

from __future__ import annotations

from collections import Counter
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

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
#: Six 60-degree ring crops (C0..C5) plus the 120-degree nadir crop C6
#: (docs/device-responsibilities.md crop table; PRD x4-sphere-coverage).
CROP_COUNT = 7
#: Shepherd stage 4, verbatim: 16 vertical columns per view. Three forward
#: crops of 16 are the bridge's 48-column ring over -90..+90 (plan Sec 3).
COLUMNS_PER_CROP = 16
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
# A gap-profile column mean: raw relative depth, so finite but on no fixed
# scale and of either sign (plan Sec 1.1 -- see GapProfile). Lenient for the
# same reason as LenientFloat: a column that comes out exactly 1 arrives as 1.
DepthColumn = Annotated[float, Field(strict=False, allow_inf_nan=False)]
# The recovered affine ground scale, `d = ground_scale / (z - ground_offset)`.
# `gt=0`: the model returns *inverse* depth, so the scale is disparity per
# metre and a non-positive one means the fit found no floor. Lenient for the
# same ruling-5 reason as everything else numeric here.
GroundScale = Annotated[float, Field(gt=0.0, strict=False, allow_inf_nan=False)]
# The model's own zero -- the value its output tends to at infinite distance.
# Measured anywhere in -1..+4 on real crops, so no bound but finiteness.
GroundOffset = Annotated[float, Field(strict=False, allow_inf_nan=False)]


def crop_field_name(index: int) -> str:
    """Multipart field name for crop ``index`` (``crop_0`` .. ``crop_6``)."""
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


class GapProfile(BaseModel):
    """One crop's depth column profile (step 21's shape; step 22 fills it).

    Shepherd's stage 4 -- 16 vertical columns, mean depth per column -- on
    one of our forward crops. Only C5/C0/C1 are ever populated (plan Sec 1.2:
    depth on six crops costs twice what steering needs, and you steer where
    you walk), but that is step 22's policy, not this contract's: the field
    is a crop index, so its bound is the crop count.

    ``columns`` is **raw** relative depth, deliberately unbounded in sign and
    magnitude. Depth Anything V2 returns relative inverse depth on no fixed
    scale, and plan Sec 1.1 forbids per-frame min-max normalisation because
    the nearest thing in frame always normalises to 1.0 -- which would make
    an empty corridor look exactly as dangerous as a wall. A 0..1 bound here
    would silently force that normalisation on the producer, so there is
    none; the scale is recovered geometrically instead, and
    ``ground_fit_residual``/``anchored`` are how the bridge learns whether
    that recovery can be trusted (Sec 2.3).

    **The scale itself (step 27).** ``ground_scale`` and ``ground_offset`` are
    the recovered affine pair: ``d_m = ground_scale / (z - ground_offset)`` for
    a raw column ``z``. Two numbers and not one, because Depth Anything V2 is
    affine-invariant in disparity -- its output is ``a/d + b`` with both
    unknowns moving per inference -- and the offset dominates: measured ``b``
    2.5..3.8 against ``a`` 0.9..3.7 on one real frame, where assuming ``b = 0``
    put a 3.49 m row at 2.92 m (plan Sec 2.5). They are **per crop**, which is
    what this model already is: each forward crop is its own inference, so each
    has its own pair, and one pair per frame would convert two crops with a
    third crop's units.

    Validity is three-valued and deliberately so:

    * **absent** (both ``None``) -- an old cloud, or a crop where no fit was
      possible at all. There are no metres and the bridge falls back to its
      scale-free rule. This is why they are optional: a response from before
      step 27 must keep parsing (plan Sec 4.1, additive only).
    * **present, ``anchored = False``** -- a fit exists but failed the residual
      guard. The numbers travel so they can be logged; they may not be used.
    * **present, ``anchored = True``** -- metres.

    Half a pair is never legal (``_affine_pair_is_whole``): ``ground_scale``
    alone *is* the one-parameter fit the measurement refuted, and
    ``ground_offset`` alone converts nothing. Note that ``anchored = True`` with
    no pair still parses -- an old cloud may say exactly that -- and the bridge
    is what declines to read it as metres, because a 422 on a legal older
    response would be a worse failure than a missing anchor.

    Timeless like everything else the cloud emits: no clock field, no
    bearing, no metres. ``t_capture_ms`` is echoed once, on the response.
    """

    model_config = _STRICT

    crop_index: Annotated[int, Field(ge=CROP_INDEX_MIN, le=CROP_INDEX_MAX)]
    columns: Annotated[
        tuple[DepthColumn, ...],
        Field(min_length=COLUMNS_PER_CROP, max_length=COLUMNS_PER_CROP),
    ]
    band_rows: tuple[Annotated[int, Field(ge=0)], Annotated[int, Field(ge=0)]]
    ground_fit_residual: Annotated[float, Field(ge=0.0, allow_inf_nan=False)]
    anchored: bool
    ground_scale: GroundScale | None = None
    ground_offset: GroundOffset | None = None

    @property
    def has_affine_scale(self) -> bool:
        """Whether this profile can convert a raw column into metres at all."""
        return self.ground_scale is not None and self.ground_offset is not None

    @model_validator(mode="after")
    def _affine_pair_is_whole(self) -> GapProfile:
        """Both numbers or neither -- ``d = a/(z - b)`` needs the pair."""
        if (self.ground_scale is None) != (self.ground_offset is None):
            raise ValueError(
                "ground_scale and ground_offset travel together: got "
                f"scale={self.ground_scale}, offset={self.ground_offset}"
            )
        return self

    @model_validator(mode="after")
    def _band_is_a_band(self) -> GapProfile:
        """``(top, bottom)`` pixel rows the vertical obstacle-band filter kept.
        ``top == bottom`` is an empty band -- the filter kept nothing, so there
        is no evidence here -- and ``top > bottom`` is an upside-down frame.
        Either is a producer bug worth a 422, not something to steer from.
        """
        top, bottom = self.band_rows
        if top >= bottom:
            raise ValueError(
                f"band_rows must be (top, bottom) with top < bottom, got {self.band_rows}"
            )
        return self


class InferenceResponse(BaseModel):
    """What ``POST /infer`` answers. ``t_capture_ms`` is echoed, never recomputed.

    ``gap_profiles`` defaults to empty and stays that way whenever the depth
    head is absent or failed to load: a response with detections and no
    profile is the honest answer, and it is also what an older cloud returns,
    so the bridge parses both without a schema bump (step 21, additive only).
    """

    model_config = _STRICT

    schema_version: Literal[1] = Field(alias="schema")
    t_capture_ms: Annotated[int, Field(ge=T_CAPTURE_MS_MIN, le=T_CAPTURE_MS_MAX)]
    inference_ms: Annotated[float, Field(ge=0.0, allow_inf_nan=False)]
    detections: tuple[Detection, ...] = ()
    gap_profiles: tuple[GapProfile, ...] = ()

    @model_validator(mode="after")
    def _one_gap_profile_per_crop(self) -> InferenceResponse:
        """Plan Sec 3.1 ruling 3: at most one ``GapProfile`` per ``crop_index``.

        Consumers index the ring **by** ``crop_index``, never by list order, so
        a duplicate would either overwrite a real crop's 16 columns or be
        silently ignored depending on which way the consumer's loop happened to
        be written -- and both are wrong steering, not a parse error anyone
        would notice. The list stays unordered and may be incomplete: a crop
        whose depth failed is simply absent (step 22 emits no zero profiles).
        """
        counts = Counter(profile.crop_index for profile in self.gap_profiles)
        duplicates = sorted(index for index, count in counts.items() if count > 1)
        if duplicates:
            raise ValueError(f"more than one gap_profile for crop_index {duplicates}")
        return self


class InferenceRequest(BaseModel):
    """``meta`` + exactly ``CROP_COUNT`` JPEG crops, ready to be posted as multipart."""

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
