"""Three 16-column depth profiles -> one 48-column ring in torso bearings.

Shepherd's stage 4 (plan Sec 0 row 4) widened from their single ~60 degree
view to our three forward crops. Two decisions in here are load-bearing:

**Bearings are composed exactly as a detection's is.** ``events.py`` turns a
box into ``yaw + (cx - 0.5) * FOV + delta``, normalised into (-180, 180]; a
ring column does the same with the column's own centre as ``cx``. Anything
else and the wearer would be told the gap and the cyclist are in different
places when they are in the same place.

**The kernel runs over the assembled ring, not over each crop.** Smoothing a
crop on its own tapers its edge columns toward nothing, which reads back as
an obstacle at exactly -30 and +30 degrees -- a phantom wall at every seam,
in the two directions a wearer most needs to trust. Assembling first is what
lets a gap that straddles a seam stay one gap (step 23 acceptance 2).

**Every column carries both frames.** ``bearing_deg`` is the wearer-frame
bearing the phone is told about; ``camera_bearing_deg`` is the same column
before ``delta_deg`` and before normalisation, and it is the one the +-90
invariant is asserted on (plan Sec 3.3 ruling 1). Keeping it rather than
re-deriving it by subtracting ``delta_deg`` back out is the point: a
subtraction reads whatever ``normalize_bearing_deg`` already folded, which is
exactly how the first version of that invariant could be satisfied by a ring
that had left the forward arc.

The "ring" is an arc, not a circle: the rear crops carry no depth (plan
Sec 1.2), so the outermost columns have no outboard neighbour and the kernel
is renormalised over the taps that exist rather than wrapping into a bearing
180 degrees away or padding with a zero that would mean "infinitely near".
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, replace
from typing import Final, Iterable, Mapping

from cane.bridge.dewarp import CROP_FOV_DEG
from cane.bridge.events import normalize_bearing_deg
from cane.cloud.api_models import COLUMNS_PER_CROP, GapProfile

logger = logging.getLogger(__name__)

#: Plan Sec 3: crop indices 5, 0, 1 -- yaw centres -60, 0, +60 -- listed
#: left to right so that ring index order *is* bearing order.
DEPTH_CROP_INDICES: Final[tuple[int, ...]] = (5, 0, 1)
RING_COLUMNS: Final = len(DEPTH_CROP_INDICES) * COLUMNS_PER_CROP
COLUMN_WIDTH_DEG: Final = CROP_FOV_DEG / COLUMNS_PER_CROP
RING_ARC_DEG: Final = RING_COLUMNS * COLUMN_WIDTH_DEG
#: Shepherd stage 4, verbatim.
SMOOTH_KERNEL: Final[tuple[float, ...]] = (0.25, 0.5, 0.25)
SMOOTH_CENTRE: Final = len(SMOOTH_KERNEL) // 2
#: A raw relative depth at or below this is not a near obstacle, it is an
#: absence of evidence: the cloud's columns are inverse depth of either sign
#: (api_models.GapProfile), and a band filter that kept nothing for a column
#: has nothing to report. Treating it as 0 metres would invent a wall.
NO_EVIDENCE_DEPTH: Final = 0.0


@dataclass(frozen=True, slots=True)
class RingColumn:
    """One 3.75-degree column: where it points, who saw it, how deep it read.

    ``bearing_deg`` is where the *wearer* is told to look: the camera-frame
    bearing plus ``delta_deg``, normalised. ``camera_bearing_deg`` is the
    column as the camera saw it -- no mount offset, never normalised -- which
    is the frame plan Sec 3.3 ruling 1's +-90 invariant is asserted in, and an
    un-normalised number is one nothing can fold into looking legal.

    ``depth`` is **raw** relative depth (plan Sec 1.1 forbids per-frame
    normalisation) or ``None`` for "no evidence here" -- a crop whose depth
    failed, or a column the vertical band filter emptied.
    """

    bearing_deg: float
    camera_bearing_deg: float
    crop_index: int
    depth: float | None


@dataclass(frozen=True, slots=True)
class GapRing:
    """The whole forward arc, left to right. Frozen: every step returns a new one."""

    columns: tuple[RingColumn, ...]

    def __post_init__(self) -> None:
        if len(self.columns) != RING_COLUMNS:
            raise ValueError(
                f"a ring is {RING_COLUMNS} columns, got {len(self.columns)}"
            )

    @property
    def has_evidence(self) -> bool:
        """False when every column is absent: there is nothing to steer from
        and the honest answer downstream is no frame at all.
        """
        return any(column.depth is not None for column in self.columns)

    @property
    def camera_arc_max_deg(self) -> float:
        """How far the arc reaches in **camera** bearings, plan Sec 3.3 ruling 1.

        Taken over all 48 columns, evidence or not: how wide the arc is, is a
        fact about the yaw table and the column geometry, not about what the
        depth head happened to see this tick. Scoping it to the columns that
        carry depth would let an unlucky pattern of absences hide an
        assembly bug for a frame at a time.
        """
        return max(abs(column.camera_bearing_deg) for column in self.columns)

    @property
    def crop_indices(self) -> frozenset[int]:
        """Which crops actually contributed -- for logs and for tests."""
        return frozenset(
            column.crop_index for column in self.columns if column.depth is not None
        )


def column_cx(column: int) -> float:
    """The column's centre in its crop's normalised coordinates."""
    return (column + 0.5) / COLUMNS_PER_CROP


def column_index_for_cx(cx: float) -> int:
    """Which column a detection's centre falls in (used by the Sec 2.3 check)."""
    return min(COLUMNS_PER_CROP - 1, max(0, int(cx * COLUMNS_PER_CROP)))


def column_camera_bearing_deg(
    crop_index: int, column: int, yaws_deg: tuple[float, ...]
) -> float:
    """``yaw + (cx - 0.5) * FOV``: the column in the camera's own frame.

    Deliberately **not** normalised and deliberately without ``delta_deg``.
    This is the number plan Sec 3.3 ruling 1's +-90 invariant is asserted on,
    so it has to be the raw sum: a wrong yaw table that puts a forward crop at
    +340 must read as +340 here and not as the perfectly legal -20 that
    normalisation would hand back.
    """
    if crop_index >= len(yaws_deg):
        raise ValueError(
            f"crop {crop_index} has no yaw in a run of {len(yaws_deg)} crops"
        )
    return yaws_deg[crop_index] + (column_cx(column) - 0.5) * CROP_FOV_DEG


def column_bearing_deg(
    crop_index: int, column: int, yaws_deg: tuple[float, ...], delta_deg: float
) -> float:
    """``yaw + (cx - 0.5) * FOV + delta``, normalised -- ``events.py``'s line.

    Kept as its own function rather than calling ``bearing_for_detection`` so
    that no ``Detection`` has to be fabricated to ask a geometric question;
    a test asserts the two agree column by column. Built on top of
    :func:`column_camera_bearing_deg` so the emitted bearing is the camera one
    rotated by the mount offset and can never be a *different* geometry.
    """
    return normalize_bearing_deg(
        column_camera_bearing_deg(crop_index, column, yaws_deg) + delta_deg
    )


def _profiles_by_crop(profiles: Iterable[GapProfile]) -> Mapping[int, GapProfile]:
    """Index by ``crop_index``, never by list order (plan Sec 3.1 ruling 3).

    Step 22 validates uniqueness, so a duplicate here is a broken producer;
    the bridge still has to answer this tick, so the first wins and says so.
    A profile for a rear crop is dropped the same way: depth only runs
    forward, and quietly binning it into a forward column would steer the
    wearer with evidence from behind them.
    """
    by_crop: dict[int, GapProfile] = {}
    for profile in profiles:
        if profile.crop_index not in DEPTH_CROP_INDICES:
            logger.warning(
                "ignoring a gap profile for crop %d: depth runs on %s only",
                profile.crop_index,
                list(DEPTH_CROP_INDICES),
            )
            continue
        if profile.crop_index in by_crop:
            logger.warning(
                "duplicate gap profile for crop %d; keeping the first",
                profile.crop_index,
            )
            continue
        by_crop[profile.crop_index] = profile
    return by_crop


def assemble_ring(
    profiles: Iterable[GapProfile], yaws_deg: tuple[float, ...], delta_deg: float
) -> GapRing:
    """The 48 columns in bearing order, with missing arcs left absent."""
    by_crop = _profiles_by_crop(profiles)
    columns: list[RingColumn] = []
    for crop_index in DEPTH_CROP_INDICES:
        profile = by_crop.get(crop_index)
        for column in range(COLUMNS_PER_CROP):
            raw = None if profile is None else profile.columns[column]
            columns.append(
                RingColumn(
                    bearing_deg=column_bearing_deg(
                        crop_index, column, yaws_deg, delta_deg
                    ),
                    camera_bearing_deg=column_camera_bearing_deg(
                        crop_index, column, yaws_deg
                    ),
                    crop_index=crop_index,
                    depth=raw if raw is not None and raw > NO_EVIDENCE_DEPTH else None,
                )
            )
    return GapRing(columns=tuple(columns))


def smooth_ring(ring: GapRing) -> GapRing:
    """``SMOOTH_KERNEL`` across the whole arc, seams included.

    An absent column stays absent -- smoothing must not invent evidence --
    and its neighbours renormalise the kernel over the taps they do have, so
    the edge of a failed crop is a boundary, not a cliff toward zero.
    """
    depths = tuple(column.depth for column in ring.columns)
    smoothed: list[RingColumn] = []
    for index, column in enumerate(ring.columns):
        if column.depth is None:
            smoothed.append(column)
            continue
        total = 0.0
        weight = 0.0
        for offset, tap in enumerate(SMOOTH_KERNEL):
            neighbour = index + offset - SMOOTH_CENTRE
            if 0 <= neighbour < len(depths) and depths[neighbour] is not None:
                total += tap * depths[neighbour]
                weight += tap
        smoothed.append(replace(column, depth=total / weight))
    return GapRing(columns=tuple(smoothed))


def ring_from_profiles(
    profiles: Iterable[GapProfile], yaws_deg: tuple[float, ...], delta_deg: float
) -> GapRing:
    """Assemble, then smooth. The order is the point (see the module docstring)."""
    return smooth_ring(assemble_ring(profiles, yaws_deg, delta_deg))
