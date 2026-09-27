"""One crop's depth map -> sixteen raw column means + the ground anchor.

The rule this module exists to obey is plan Sec 1.1: the columns are **raw**
relative inverse depth. No min-max, no rescale, no offset removal. Depth
Anything V2 returns depth on no fixed scale, and normalising per frame would
make an empty corridor score identically to a wall, because the nearest thing
in frame always normalises to 1.0. The scale is recovered geometrically
instead, from the floor, and `ground_fit_residual`/`anchored` say whether that
recovery can be trusted.

The residual *is* a normalised quantity -- a ratio of RMS misfit to the fit's
own dynamic range -- and that is deliberate: a goodness-of-fit carrying the
model's arbitrary scale could not be thresholded at all. It is not depth, and
it never reaches a column.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Final

import numpy as np
from numpy.typing import NDArray

from cane.cloud.api_models import COLUMNS_PER_CROP, GapProfile, GroundHazardReading
from cane.cloud.depth.constants import (
    MOUNT_HEIGHT_M,
    NO_FIT_RESIDUAL,
    RESIDUAL_CAP,
    RESIDUAL_REFERENCE_M,
)
from cane.cloud.depth.geometry import band_rows, ground_rows, row_inverse_distance
from cane.cloud.depth.ground_hazard import GroundHazard, detect_ground_hazard

logger = logging.getLogger(__name__)

_MIN_FIT_SPREAD: Final = 1e-12
#: A gradient this small is numerically indistinguishable from the flat strip a
#: wall produces, and it is the denominator of every recovered distance.
_MIN_FIT_GRADIENT: Final = 1e-9


@dataclass(frozen=True, slots=True)
class GroundFit:
    """The two-parameter relative-depth-to-metres fit, and its verdict.

    `z = scale * (1/d) + offset`, so `d_m = scale / (z - offset)` for a raw
    relative depth `z`. Both numbers, because Depth Anything V2 is
    affine-invariant in disparity and the offset dominates: measured `offset`
    2.5..3.8 against a `scale` of 0.9..3.7 on one real frame, which is why plan
    Sec 2.3's one-parameter fit was rejected on all three forward crops (plan
    Sec 2.5).

    Both are `None` when there is no usable fit at all -- too few floor rows, a
    non-finite strip, or a non-positive gradient (a vertical surface, or a map
    that was not inverse depth, so the whole assumption is wrong).

    `rms` is the raw misfit in the model's own arbitrary units; it is a
    diagnostic and never travels. `residual` is the thresholdable form of the
    same number -- see :func:`fit_ground`.
    """

    scale: float | None
    offset: float | None
    rms: float
    residual: float
    rows_used: int
    anchored: bool


@dataclass(frozen=True, slots=True)
class CropDepthProfile:
    """What the cloud computed for one crop, before it is cut down to the wire.

    Step 27 closed the gap step 22 reported here: `GapProfile` now carries the
    affine pair, so the bridge can reach metres from the wire alone. Both
    numbers travel, `ground_scale` and `ground_offset`, because `d = a/(z - b)`
    needs both and one of them is not half a scale -- it is step 22's
    one-parameter fit, which the measurement refuted.
    """

    crop_index: int
    columns: tuple[float, ...]
    band_rows: tuple[int, int]
    ground_fit_residual: float
    anchored: bool
    ground_scale: float | None
    ground_offset: float | None
    ground_hazard: GroundHazardReading | None = None

    def __post_init__(self) -> None:
        if self.anchored and (self.ground_scale is None or self.ground_offset is None):
            # A producer invariant, not a contract rule: the wire must keep
            # parsing an *old* response that claims `anchored` with no scale
            # (the bridge then declines to use metres), but this process may
            # never emit one, because there would be nothing to convert with.
            raise ValueError(
                f"crop {self.crop_index}: anchored with no affine pair "
                f"(scale={self.ground_scale}, offset={self.ground_offset})"
            )

    def to_gap_profile(self) -> GapProfile:
        return GapProfile(
            crop_index=self.crop_index,
            columns=self.columns,
            band_rows=self.band_rows,
            ground_fit_residual=self.ground_fit_residual,
            anchored=self.anchored,
            ground_scale=self.ground_scale,
            ground_offset=self.ground_offset,
            ground_hazard=self.ground_hazard,
        )


def column_means(band: NDArray[np.float32]) -> tuple[float, ...]:
    """Shepherd stage 4: `COLUMNS_PER_CROP` vertical columns, mean per column.

    An even split of the crop's full width -- one column per 3.75 degrees of
    its 60 -- because step 23 turns the column index straight into a bearing.
    """
    if band.ndim != 2 or band.size == 0:
        raise ValueError(f"the band must be a non-empty HxW array, got {band.shape}")
    if band.shape[1] < COLUMNS_PER_CROP:
        raise ValueError(f"a {band.shape[1]}-px band cannot hold {COLUMNS_PER_CROP} columns")
    slices = np.array_split(band, COLUMNS_PER_CROP, axis=1)
    # Accumulated in float64, not the map's float32: 14k values summed in
    # float32 lose the low bits, and worse, the result depends on how numpy
    # happened to align its SIMD loads -- so the same band could yield two
    # different columns in two runs. The wire field is a float64 anyway.
    return tuple(float(part.mean(dtype=np.float64)) for part in slices)


def fit_ground(
    depth: NDArray[np.float32],
    crop_px: int,
    *,
    max_residual: float,
    mount_height_m: float = MOUNT_HEIGHT_M,
) -> GroundFit:
    """Fit `z = scale * (1/d) + offset` on the floor rows and judge it (Sec 2.5).

    Two parameters, not plan Sec 2.3's one, because **Depth Anything V2 is
    affine-invariant in disparity**: its output is `a/d + b` with both unknowns
    moving per inference. Step 22 measured the one-parameter fit rejected on all
    three forward crops of a real frame (residuals 0.686 / 0.922 / 0.892) while
    the affine fit scored rms 0.068 / 0.005 / 0.013 on the same data -- 4x to 70x
    better -- with `b` = 2.5 / 3.5 / 3.8 against `a` = 3.7 / 0.9 / 1.4.

    Per-row evidence is the row's **median** across columns, not its mean: the
    floor is exactly what a chair leg or a passing shoe covers, and a median
    shrugs those off.

    **This is not plan Sec 1.1's trap.** Per-frame min-max normalisation takes
    both of its numbers off the depth map, so the nearest thing in frame always
    becomes 1.0 and an empty corridor scores like a wall. Every `1/d` here comes
    from `row_inverse_distance` -- a measured mount height and a known focal
    length -- so the design matrix does not depend on the map at all, and
    poisoning the map's extremes cannot move either parameter. This is geometric
    recovery from a known plane, in the direction the plan's own Sec 2.5 asks for.

    **Two guards, and only one of them is the residual.**

    * `scale > 0`, structurally. A vertical surface filling the floor rows has
      no gradient down the frame, so least squares returns `a ~ 0` (or negative)
      -- and two free parameters fit a flat strip *perfectly*, rms 0. A residual
      test alone would therefore anchor a wall. Measured: an exactly flat strip
      gives `a = 0`, and walls tilted 0.02..0.30 all give `a < 0`.
    * The residual, which is the misfit expressed as the **fractional distance
      error it implies at `RESIDUAL_REFERENCE_M`**. Since `d = a/(z - b)` gives
      `dd/d = -(d/a) dz`, that error is `rms * d / a`: dimensionless, so it is
      thresholdable even though relative depth is not, and a statement about
      metres rather than about arbitrary units. `DEFAULT_MAX_GROUND_RESIDUAL`
      carries the derivation of the ceiling.

    What neither guard can see -- say it out loud rather than imply it -- is a
    plane at the wrong *angle* or the wrong *height*. A pitch error is absorbed
    exactly into `offset`, a mount-height error exactly into `scale`, and a
    constant up-slope into both: all three fit with a zero residual and return a
    wrong metre. Measured: a 20 % mount-height error gives a 20 % distance error
    at residual 0.000. So is a floor whose near half is covered by a crowd: it
    inflated `scale` 4x at residual 0.056, inside the ceiling. That is the
    honest limit of a scale recovered from the floor alone.
    """
    rows = ground_rows(crop_px, mount_height_m)
    if rows is None:
        return GroundFit(None, None, 0.0, NO_FIT_RESIDUAL, 0, False)
    strip = depth[rows[0] : rows[1], :]
    used = int(strip.shape[0])
    if strip.size == 0 or not bool(np.isfinite(strip).all()):
        return GroundFit(None, None, 0.0, NO_FIT_RESIDUAL, used, False)
    z = np.median(strip.astype(np.float64), axis=1)
    inverse_d = row_inverse_distance(
        np.arange(rows[0], rows[1], dtype=np.float64), crop_px, mount_height_m
    )
    centred = inverse_d - float(np.mean(inverse_d))
    leverage = float(centred @ centred)
    if leverage <= _MIN_FIT_SPREAD:
        # Every floor row would be looking at the same distance, so there is no
        # geometry to fit against -- not a bad fit, no fit.
        return GroundFit(None, None, 0.0, NO_FIT_RESIDUAL, used, False)
    scale = float(centred @ (z - float(np.mean(z)))) / leverage
    if scale <= _MIN_FIT_GRADIENT:
        # A non-positive gradient down the floor is not a bad fit, it is the
        # wrong model: relative *inverse* depth must grow towards the feet.
        return GroundFit(None, None, 0.0, NO_FIT_RESIDUAL, used, False)
    offset = float(np.mean(z)) - scale * float(np.mean(inverse_d))
    rms = float(np.sqrt(np.mean((z - (scale * inverse_d + offset)) ** 2)))
    residual = min(rms * RESIDUAL_REFERENCE_M / scale, RESIDUAL_CAP)
    return GroundFit(scale, offset, rms, residual, used, residual <= max_residual)


def build_profile(
    crop_index: int,
    depth: NDArray[np.float32],
    *,
    max_residual: float,
    mount_height_m: float = MOUNT_HEIGHT_M,
) -> CropDepthProfile | None:
    """One crop's profile, or `None` when this frame has no honest answer.

    `None` is returned -- never a zero profile -- when the crop is too small to
    hold a band or sixteen columns, or when the band contains a non-finite
    value (an fp16 overflow reaches here as NaN, and the contract rejects
    non-finite columns, so emitting one would 422 our own response).
    """
    if depth.ndim != 2 or depth.size == 0:
        raise ValueError(f"a depth map must be a non-empty HxW array, got {depth.shape}")
    if depth.shape[0] != depth.shape[1]:
        raise ValueError(f"the crops are square; got a {depth.shape} depth map")
    crop_px = int(depth.shape[0])
    rows = band_rows(crop_px, mount_height_m)
    if rows is None:
        logger.warning("crop %d: a %d-px crop holds no obstacle band", crop_index, crop_px)
        return None
    if crop_px < COLUMNS_PER_CROP:
        logger.warning("crop %d: %d px cannot hold %d columns", crop_index, crop_px, COLUMNS_PER_CROP)
        return None
    band = depth[rows[0] : rows[1], :]
    if not bool(np.isfinite(band).all()):
        logger.warning("crop %d: the depth band is not finite; emitting no profile", crop_index)
        return None
    fit = fit_ground(depth, crop_px, max_residual=max_residual, mount_height_m=mount_height_m)
    # Step 29 rides the fit rather than repeating it: the affine pair is exactly
    # what turns a scale-free map into a comparable ratio, so there is no ground
    # hazard to find without one.
    hazard = detect_ground_hazard(depth, crop_px, fit, mount_height_m=mount_height_m)
    return CropDepthProfile(
        crop_index=crop_index,
        columns=column_means(band),
        band_rows=rows,
        ground_fit_residual=fit.residual,
        anchored=fit.anchored,
        ground_scale=fit.scale,
        ground_offset=fit.offset,
        ground_hazard=_reading(hazard),
    )


def _reading(hazard: GroundHazard | None) -> GroundHazardReading | None:
    """The detector's value object as the wire model. Kept here rather than in
    `ground_hazard` so that module stays pure numpy with no pydantic."""
    if hazard is None:
        return None
    return GroundHazardReading(
        column=hazard.column,
        span=hazard.span,
        drop_ratio=hazard.drop_ratio,
        confidence=hazard.confidence,
    )
