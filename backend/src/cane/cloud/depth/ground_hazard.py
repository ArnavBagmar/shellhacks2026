"""Ground-plane discontinuities: the drop-off and the pothole (step 29).

`camera-sync-design.md` Sec 2.3 gave this up, and gave a good reason: a
drop-off is not an object, YOLO cannot box it, and monocular depth is weakest
on textureless ground. It also left the door open -- *"flag points deviating
from the fitted plane"* -- and step 27 has since shipped the fit that makes
that possible.

**What this can and cannot say.** Depth Anything V2 is affine-invariant in
disparity, measured, so there are no centimetres here and there never will be
from this sensor alone. What there is, is a *ratio*: the ground in this column
reads 1.8x farther away than a flat floor would at that row. That is enough to
say "step down, ahead to the left" and nowhere near enough to say "15 cm", so
this module reports the ratio and everything downstream is forbidden from
dressing it up.

**Why a hole reads low.** Relative *inverse* depth grows towards the feet, so
farther is smaller. Inverting the step-27 fit, `d = scale / (z - offset)`, a
cell whose recovered `d` exceeds the flat-floor `d` for its row is ground that
has fallen away.

**The honest limit, stated rather than implied.** The fit takes each row's
*median* across columns, which is what lets a hazard in one column be measured
against the floor in the others. Turn that around and it is a ceiling: a
discontinuity spanning more than half the crop width becomes the median, the
fit follows it down, and the hazard hides itself. A kerb running across the
whole frame is exactly that shape. This detector finds holes in a floor; it
does not find the edge of a floor, and Sec 2.3's position stands for that case.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Final, Protocol

import numpy as np
from numpy.typing import NDArray

from cane.cloud.api_models import COLUMNS_PER_CROP
from cane.cloud.depth.constants import MOUNT_HEIGHT_M
from cane.cloud.depth.geometry import ground_rows, row_inverse_distance

logger = logging.getLogger(__name__)


class AffineFit(Protocol):
    """Just the two numbers this module needs from step 27's ground fit.

    Structural rather than an import of `GroundFit`, for two reasons: it breaks
    what would otherwise be an import cycle with `profile`, and it says plainly
    that the detector has no business reading the fit's *verdict*. Whether the
    scale is trustworthy enough to state metres (`anchored`) is a question about
    metres, and this module never states one.
    """

    scale: float | None
    offset: float | None


#: Below this the column is not a hazard. 1.35 means "a third farther than the
#: floor should be here", which a wet patch, a shadow or a paving seam does not
#: reach and a kerb or an open pothole clears easily. Set well above 1 on
#: purpose: Sec 2.3's argument is that a half-working detector is worse than
#: none, so the cost of a miss is deliberately preferred to the cost of a false
#: alarm. A demo knob, like `UrgencyPolicy.staticActM`.
MIN_DROP_RATIO: Final = 1.35

#: The ratio at which confidence saturates. A floor reading three times its
#: expected distance is a hole however you measure it.
FULL_CONFIDENCE_RATIO: Final = 3.0

#: A recovered distance needs `z` above the model's offset; at or below it the
#: fit puts the surface at infinity, which is no evidence rather than a hole.
_MIN_DENOMINATOR: Final = 1e-9


@dataclass(frozen=True, slots=True)
class GroundHazard:
    """One crop's worst ground discontinuity."""

    #: Which of the crop's `COLUMNS_PER_CROP` columns is worst. The caller turns
    #: this into a bearing exactly as it does for the gap ring.
    column: int
    #: How many columns cleared the threshold. One column is a pothole; several
    #: is a trench, a kerb line, or the edge of the pavement.
    span: int
    #: Recovered distance over flat-floor distance at the worst column. Always
    #: above `MIN_DROP_RATIO`. **Not a depth.**
    drop_ratio: float
    confidence: float
    rows_used: int


def detect_ground_hazard(
    depth: NDArray[np.float32],
    crop_px: int,
    fit: AffineFit,
    *,
    mount_height_m: float = MOUNT_HEIGHT_M,
) -> GroundHazard | None:
    """The worst descending discontinuity in this crop's floor, or `None`.

    `None` is the answer for every uncertain input, never a low-confidence
    hazard: there is no threshold the consumer could apply that this function
    cannot apply better, and Sec 2.3 is explicit about which way to err.
    """
    if crop_px <= 0 or fit.scale is None or fit.offset is None:
        return None
    if depth.ndim != 2 or depth.size == 0:
        return None

    rows = ground_rows(crop_px, mount_height_m)
    if rows is None:
        return None
    if depth.shape[0] < rows[1] or depth.shape[1] < COLUMNS_PER_CROP:
        return None

    strip = depth[rows[0] : rows[1], :].astype(np.float64)
    if strip.size == 0 or not bool(np.isfinite(strip).all()):
        return None

    inverse_d = row_inverse_distance(
        np.arange(rows[0], rows[1], dtype=np.float64), crop_px, mount_height_m
    )
    # Rows at or above the horizon have no flat-floor distance to compare to.
    usable_rows = inverse_d > _MIN_DENOMINATOR
    if not bool(usable_rows.any()):
        return None

    ratio = _distance_ratio(strip[usable_rows], inverse_d[usable_rows], fit)
    medians = _column_medians(ratio)

    # NaN compares false, so an all-unusable column can never be the worst.
    exceeded = medians >= MIN_DROP_RATIO
    if not bool(exceeded.any()):
        return None

    worst = int(np.argmax(np.where(exceeded, medians, -np.inf)))
    drop_ratio = float(medians[worst])
    return GroundHazard(
        column=worst,
        span=int(exceeded.sum()),
        drop_ratio=drop_ratio,
        confidence=_confidence(drop_ratio),
        rows_used=int(usable_rows.sum()),
    )


def _distance_ratio(
    strip: NDArray[np.float64], inverse_d: NDArray[np.float64], fit: AffineFit
) -> NDArray[np.float64]:
    """Recovered distance over flat-floor distance, per cell.

    This is what needs step 27's *two*-parameter fit: with only a scale, the
    offset would leak straight into the ratio and every floor would look like a
    hazard at one distance and none at another.
    """
    assert fit.scale is not None and fit.offset is not None
    denominator = strip - fit.offset
    # `d = scale / (z - offset)`; a non-positive denominator is beyond the
    # model's horizon, so it contributes no evidence rather than a negative
    # distance that would read as a hole.
    with np.errstate(divide="ignore", invalid="ignore"):
        observed = np.where(
            denominator > _MIN_DENOMINATOR, fit.scale / denominator, np.nan
        )
    flat = 1.0 / inverse_d
    return observed / flat[:, None]


def _column_medians(ratio: NDArray[np.float64]) -> NDArray[np.float64]:
    """One median per column. Median, not mean, for the same reason the fit
    uses one: a single bright or dead pixel must not invent a hazard."""
    medians = np.empty(COLUMNS_PER_CROP, dtype=np.float64)
    for index, part in enumerate(np.array_split(ratio, COLUMNS_PER_CROP, axis=1)):
        finite = part[np.isfinite(part)]
        medians[index] = float(np.median(finite)) if finite.size else np.nan
    return medians


def _confidence(drop_ratio: float) -> float:
    span = FULL_CONFIDENCE_RATIO - MIN_DROP_RATIO
    return float(min(max((drop_ratio - MIN_DROP_RATIO) / span, 0.0), 1.0))
