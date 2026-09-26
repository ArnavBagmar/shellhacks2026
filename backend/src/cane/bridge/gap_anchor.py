"""Relative depth -> metres, and whether that conversion may be trusted.

Plan Sec 2.3 as amended by Sec 2.5, and the plan's own acknowledged soft spot
(Risks 1). Depth Anything V2 returns relative *inverse* depth on no fixed
scale, so a metre has to be recovered geometrically: the X4 sits at a known
height and the crops have a known focal length, so the ground rows give an
**affine** fit ``depth = a/d + b``, inverted here as ``d = a / (depth - b)``.

**Why two numbers, and why one was not enough.** Step 22 ran Sec 2.3's
one-parameter fit against a real X4 equirect and *all three* forward crops were
rejected (residuals 0.686 / 0.922 / 0.892 against its ceiling), because Depth
Anything V2 is affine-invariant in disparity and the offset dominates: measured
``b`` = 2.5 / 3.5 / 3.8 against ``a`` = 3.7 / 0.9 / 1.4. A fit that assumes
``b = 0`` cannot recover scale; the affine fit scored rms 0.068 / 0.005 / 0.013
on the same data. Step 27 fitted both and widened ``GapProfile`` to carry them,
which is what turned the metric path on.

**The pair is per crop, not per frame.** Each forward crop is a separate
inference, so each has its own affine freedom -- the numbers above are three
pairs from *one* frame. :class:`DepthAnchor` therefore holds one
:class:`CropScale` per crop and converts each column in its own crop's units.
The *verdict*, though, stays per frame: ``d_min`` is taken across the whole arc,
so one number cannot be half in metres (Sec 2.3's own "the frame's anchor").

This also means the ring's raw depths are **not** comparable across crops, which
matters beyond this module: a scene at one constant distance reads 3.12 / 3.65 /
4.04 on C5 / C0 / C1 under the measured pairs. Converting per crop, as here, is
what makes the 48 columns one ring; ``docs/steps/step-27-brief.md`` records where
that is still imperfect (``smooth_ring`` runs over raw depth across the seams).

``RELATIVE_DEPTH_SCALE_M`` survives only as the fallback for a frame whose
profiles carry no pair at all. It drives nothing that runs: a response with no
scale is ``AnchorVerdict.NO_SCALE``, i.e. the unanchored path, because
substituting a placeholder for a measurement is the silent fake step 8 forbids.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, replace
from enum import StrEnum
from typing import Final, Iterable, Sequence

from cane.bridge.gap_ring import (
    DEPTH_CROP_INDICES,
    NO_EVIDENCE_DEPTH,
    column_index_for_cx,
)
from cane.cloud.api_models import Detection, GapProfile
from cane.cloud.motion import band_for_range
from cane.contracts.awareness_event import RangeBand

logger = logging.getLogger(__name__)

#: The ``scale`` in ``d = scale / depth``, in metre-depth-units.
#:
#: A placeholder, not a measurement, kept only as the default for a caller that
#: supplies no pair: since step 27 the real pair arrives per crop on
#: ``GapProfile`` and a frame that carries none is refused (``NO_SCALE``) rather
#: than converted with this. Plan Sec 4.5's timing run is the only raw output it
#: was ever derived from (1.935 .. 3.011 arbitrary inverse-depth units on a
#: 640 px crop, which 1.8 maps onto 0.93 .. 0.60 m).
RELATIVE_DEPTH_SCALE_M: Final = 1.8
#: The ``b`` of ``depth = a/d + b`` when no pair was supplied. Zero is the
#: one-parameter special case, i.e. exactly the assumption the measurement
#: refuted -- which is another reason nothing live may rely on this default.
RELATIVE_DEPTH_OFFSET: Final = 0.0
#: Beyond this the ramp is saturated at "clear" anyway, and an inverse that
#: is about to divide by ~0 must not become an infinity that poisons a min().
DISTANCE_MAX_M: Final = 10.0
#: Defence in depth behind the producer's own ``anchored`` verdict, and
#: deliberately looser than it. Step 22's residual is a *ratio* (its own
#: ceiling is ``CloudSettings.depth_max_ground_residual`` = 0.25, and live
#: crops measured 0.69 .. 0.92), so 1.0 means "the fit is no better than the
#: signal it is fitting". Duplicating the producer's 0.25 here would put the
#: same judgement in two places and let them drift; this only catches a fit
#: that has blown up while still claiming ``anchored``.
GROUND_FIT_RESIDUAL_MAX: Final = 1.0
#: Sec 2.3: "disagreement by more than one band invalidates the frame's
#: anchor". Bands are near <= 5 m, mid 5-15 m, far > 15 m.
MAX_BAND_DISAGREEMENT: Final = 1
_BAND_ORDER: Final[tuple[RangeBand, ...]] = (RangeBand.NEAR, RangeBand.MID, RangeBand.FAR)


class AnchorVerdict(StrEnum):
    """Why the frame's scale may or may not be read as metres."""

    ACCEPTED = "accepted"
    NO_PROFILE = "no_profile"
    PRODUCER_UNANCHORED = "producer_unanchored"
    RESIDUAL_TOO_HIGH = "residual_too_high"
    BAND_DISAGREEMENT = "band_disagreement"
    #: The producer stands behind its fit but sent no affine pair -- a cloud
    #: from before step 27. There is nothing to convert with, and borrowing
    #: ``RELATIVE_DEPTH_SCALE_M`` would be a fabricated metre.
    NO_SCALE = "no_scale"


@dataclass(frozen=True, slots=True)
class CropScale:
    """One crop's fitted affine pair: ``d = scale_m / (depth - offset)``.

    Per crop because Depth Anything V2's affine freedom is per inference, and
    each forward crop is its own inference (measured: three unrelated pairs from
    one frame).
    """

    crop_index: int
    scale_m: float
    offset: float

    def __post_init__(self) -> None:
        if not self.scale_m > 0.0:
            raise ValueError(
                f"crop {self.crop_index}: an anchor scale must be positive, got {self.scale_m}"
            )

    def distance_m(self, depth: float | None) -> float | None:
        return distance_for_depth(depth, self.scale_m, self.offset)


@dataclass(frozen=True, slots=True)
class DepthAnchor:
    """One frame's depth-to-metres conversion, plus whether to trust it.

    The *verdict* is per frame, not per crop, because ``d_min`` is taken across
    the whole arc: a per-crop verdict would mix a trusted metre and an untrusted
    one inside one number that the phone reads as one number. Sec 2.3's own
    wording -- "invalidates the frame's anchor" -- is this reading.

    The *conversion* is per crop (``crop_scales``), because the affine pair is.
    ``scale_m``/``offset`` are the fallback for a crop with no pair of its own,
    and they are a placeholder rather than a measurement -- which is why a frame
    whose profiles carry no pair is refused outright instead of being converted
    with them.
    """

    scale_m: float
    verdict: AnchorVerdict
    offset: float = RELATIVE_DEPTH_OFFSET
    crop_scales: tuple[CropScale, ...] = ()

    def __post_init__(self) -> None:
        if not self.scale_m > 0.0:
            raise ValueError(f"an anchor scale must be positive, got {self.scale_m}")

    @property
    def accepted(self) -> bool:
        return self.verdict is AnchorVerdict.ACCEPTED

    def scale_for(self, crop_index: int | None) -> tuple[float, float]:
        """The pair to read ``crop_index`` with, falling back to the frame's."""
        for scale in self.crop_scales:
            if scale.crop_index == crop_index:
                return scale.scale_m, scale.offset
        return self.scale_m, self.offset

    def distance_m(self, depth: float | None, crop_index: int | None = None) -> float | None:
        """Metres for one column, or ``None`` when it holds no evidence.

        Converts even when rejected: the ranking is still usable and the
        caller is the one that must downgrade ``proximity`` (Sec 1.1).
        """
        scale_m, offset = self.scale_for(crop_index)
        return distance_for_depth(depth, scale_m, offset)


def depth_for_distance_m(
    distance_m: float,
    scale_m: float = RELATIVE_DEPTH_SCALE_M,
    offset: float = RELATIVE_DEPTH_OFFSET,
) -> float:
    """The inverse of :func:`distance_for_depth`: metres -> raw relative depth.

    Used to script rings in tests and to state a calibration target in one
    direction rather than two.
    """
    if not distance_m > 0.0:
        raise ValueError(f"a distance must be positive, got {distance_m}")
    return scale_m / distance_m + offset


def distance_for_depth(
    depth: float | None,
    scale_m: float = RELATIVE_DEPTH_SCALE_M,
    offset: float = RELATIVE_DEPTH_OFFSET,
) -> float | None:
    """``scale / (depth - offset)``, saturated, with three distinct outcomes.

    * ``None`` -- **absence**. A non-positive raw column is the cloud's own
      "the band kept nothing here" convention, and "we did not see" is never
      "it is clear" (step 8's no-silent-fake rule).
    * ``DISTANCE_MAX_M`` -- **the sky**. ``depth -> offset`` as ``d ->
      infinity``, so ``depth <= offset`` is at or beyond the model's horizon.
      That column *was* looked at and the answer is "very far", which is a
      different fact from absence and must not be folded into it.
    * a metre, saturated at ``DISTANCE_MAX_M`` -- beyond that the ramp reads
      "clear" anyway, and an inverse about to divide by ~0 must not become an
      infinity that poisons a ``min()``.
    """
    if depth is None or depth <= NO_EVIDENCE_DEPTH:
        return None
    excess = depth - offset
    if excess <= 0.0:
        return DISTANCE_MAX_M
    return min(DISTANCE_MAX_M, scale_m / excess)


#: The one affine pair the whole ring is converted into once the per-crop pairs
#: are known: ``(1.0, 0.0)``, i.e. the columns become plain **inverse metres**.
#: Not an arbitrary reference -- it is the only one that needs no units of its
#: own, so ``distance = 1 / column`` and nothing downstream carries a scale.
INVERSE_METRE_SCALE: Final = 1.0
INVERSE_METRE_OFFSET: Final = 0.0


def inverse_metre_columns(
    profile: GapProfile, anchor: DepthAnchor
) -> tuple[float, ...]:
    """One crop's raw columns, re-expressed as ``1/d`` in metres.

    **Why this exists, and it is not cosmetic.** The three forward crops are
    three inferences with three unrelated affine pairs, so their raw columns are
    not comparable -- and ``smooth_ring`` runs the kernel *across the seams*,
    which means the two columns either side of each seam were a blend of two
    different unit systems. Measured on a scene at a uniform 3 m with the real
    pairs, a seam column came back as 2.03 m: a 32 % error, and because ``d_min``
    is taken over the whole arc it set ``proximity`` for the entire frame.

    Converting each crop with its own pair *before* assembly removes the problem
    at its root rather than patching the seams: after this, all 48 columns are one
    metric space, the kernel is averaging like with like, and the ring needs no
    scale at all (``distance = 1/column``).

    Inverse metres rather than metres because that is the quantity the ring has
    always smoothed, and averaging inverse depth leans on the *nearer* neighbour
    -- the conservative direction for a hazard signal. The absence convention is
    preserved exactly: a column with no evidence stays non-positive, and a column
    at or beyond the model's horizon saturates to ``1/DISTANCE_MAX_M`` rather than
    to a zero that would read as absence.
    """
    scale_m, offset = anchor.scale_for(profile.crop_index)
    columns: list[float] = []
    for raw in profile.columns:
        distance = distance_for_depth(raw, scale_m, offset)
        columns.append(NO_EVIDENCE_DEPTH if distance is None else 1.0 / distance)
    return tuple(columns)


def to_inverse_metres(
    profiles: Iterable[GapProfile], anchor: DepthAnchor
) -> tuple[GapProfile, ...]:
    """Every profile's columns in inverse metres, carrying the trivial pair.

    Only meaningful with an accepted anchor; the caller checks. A rejected frame
    keeps its raw columns untouched, because the scale-free rule is a ranking
    inside each crop and has no metre to be converted into.
    """
    return tuple(
        profile.model_copy(
            update={
                "columns": inverse_metre_columns(profile, anchor),
                "ground_scale": INVERSE_METRE_SCALE,
                "ground_offset": INVERSE_METRE_OFFSET,
            }
        )
        for profile in profiles
    )


def inverse_metre_anchor(anchor: DepthAnchor) -> DepthAnchor:
    """The same verdict, reading a ring that is already in inverse metres."""
    return DepthAnchor(
        scale_m=INVERSE_METRE_SCALE,
        verdict=anchor.verdict,
        offset=INVERSE_METRE_OFFSET,
        crop_scales=(),
    )


def band_distance(left: RangeBand, right: RangeBand) -> int:
    """How many bands apart two range bands are (0, 1 or 2)."""
    return abs(_BAND_ORDER.index(left) - _BAND_ORDER.index(right))


def _disagreeing_detection(
    detections: Iterable[Detection],
    profiles: Sequence[GapProfile],
    anchor: DepthAnchor,
) -> tuple[Detection, RangeBand, float] | None:
    """Sec 2.3's second guard: step 7's pinhole band vs this frame's depth.

    Only detections in a crop that *has* a profile, over a column that has
    evidence, and carrying a band at all can contradict anything. The depth is
    read through ``anchor``, so each crop is converted in its own affine units --
    reading one crop with another's pair is how a nearly vacuous guard (Sec 3.3
    ruling 4) would become a wrong one.
    """
    by_crop = {profile.crop_index: profile for profile in profiles}
    for detection in detections:
        profile = by_crop.get(detection.crop_index)
        if profile is None or detection.range_band is None:
            continue
        column = column_index_for_cx(detection.cx)
        distance = anchor.distance_m(profile.columns[column], detection.crop_index)
        if distance is None:
            continue
        by_depth = band_for_range(distance)
        if band_distance(by_depth, detection.range_band) > MAX_BAND_DISAGREEMENT:
            return detection, by_depth, distance
    return None


def _profile_verdict(profiles: Sequence[GapProfile]) -> AnchorVerdict:
    """The order is the contract: an older verdict must not become a newer one.

    ``PRODUCER_UNANCHORED`` is checked before ``NO_SCALE`` so a profile that
    carries a *rejected* pair reads exactly as it did before step 27.
    """
    if not profiles:
        return AnchorVerdict.NO_PROFILE
    if any(not profile.anchored for profile in profiles):
        return AnchorVerdict.PRODUCER_UNANCHORED
    if any(profile.ground_fit_residual > GROUND_FIT_RESIDUAL_MAX for profile in profiles):
        return AnchorVerdict.RESIDUAL_TOO_HIGH
    if any(not profile.has_affine_scale for profile in profiles):
        # An old cloud: it stands behind a fit it cannot transmit. One crop
        # without a pair costs the whole frame, for the same reason a rejected
        # residual does -- ``d_min`` spans crops.
        return AnchorVerdict.NO_SCALE
    return AnchorVerdict.ACCEPTED


def _crop_scales(profiles: Sequence[GapProfile]) -> tuple[CropScale, ...]:
    """One :class:`CropScale` per profile that actually carries a pair.

    A profile without one contributes nothing rather than a default, so a crop
    with no scale can only ever be read through the anchor's explicit fallback --
    and the verdict has already refused the frame by then.
    """
    scales: list[CropScale] = []
    for profile in profiles:
        if profile.ground_scale is None or profile.ground_offset is None:
            continue
        scales.append(
            CropScale(
                crop_index=profile.crop_index,
                scale_m=profile.ground_scale,
                offset=profile.ground_offset,
            )
        )
    return tuple(scales)


def anchor_for_frame(
    profiles: Iterable[GapProfile],
    detections: Iterable[Detection],
    scale_m: float = RELATIVE_DEPTH_SCALE_M,
    offset: float = RELATIVE_DEPTH_OFFSET,
) -> DepthAnchor:
    """Both Sec 2.3 guards, over the forward profiles of one response.

    ``scale_m``/``offset`` are only the fallback for a crop that sent no pair;
    the pairs the profiles carry are what a live frame is converted with.
    """
    forward = tuple(
        profile for profile in profiles if profile.crop_index in DEPTH_CROP_INDICES
    )
    verdict = _profile_verdict(forward)
    anchor = DepthAnchor(
        scale_m=scale_m,
        verdict=verdict,
        offset=offset,
        crop_scales=_crop_scales(forward),
    )
    if verdict is AnchorVerdict.ACCEPTED:
        clash = _disagreeing_detection(detections, forward, anchor)
        if clash is not None:
            culprit, by_depth, distance = clash
            logger.info(
                "anchor rejected: box %s in crop %d is %s by pinhole but %s "
                "(%.2f m) by depth -- more than %d band apart (Sec 2.3)",
                culprit.track_id,
                culprit.crop_index,
                culprit.range_band,
                by_depth,
                distance,
                MAX_BAND_DISAGREEMENT,
            )
            return replace(anchor, verdict=AnchorVerdict.BAND_DISAGREEMENT)
    return anchor
