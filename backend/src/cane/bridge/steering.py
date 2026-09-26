"""Passability, the gap, and Shepherd's steering law (plan Sec 2.2, step 23 B/C).

Pure: a :class:`~cane.bridge.gap_ring.GapRing` and a
:class:`~cane.bridge.gap_anchor.DepthAnchor` in, one
:class:`SteeringSolution` out. No clock, no socket, no pipeline -- the
geometry is hard enough to get right without a loop around it.

The law is Shepherd's stage 5, unchanged in shape::

    proximityFactor = clamp((d_clear - d_min) / (d_clear - d_stop), 0, 1)
    command         = sign(gapDirection) * sqrt(abs(gapDirection)) * proximityFactor

**There are two passability rules, and the scale-free one is the one that
runs today.** Step 22 measured the plan Sec 2.3 ground fit being rejected on
all three forward crops of a real X4 frame (residuals 0.69 / 0.92 / 0.89):
Depth Anything V2 is affine-invariant in disparity, ``v = a/d + b`` with both
unknowns moving per frame, so a one-parameter fit cannot recover a metre and
``GapProfile`` carries no scale field to send one over anyway. So:

* **anchored** (no live evidence yet, kept behind a clean seam): distances in
  metres, ``D_STOP_M`` for passability, a body's width at the run's own
  distance, and Shepherd's ramp for ``proximity``.
* **unanchored** (the live path): :func:`relative_freedom`, a ratio of
  differences in which *both* ``a`` and ``b`` cancel, so it is exactly as
  valid as the ranking plan Sec 1.1 permits -- and ``proximity`` is one named
  constant, never a metre laundered through a band.

Three other choices are ours and each is defended where it is made: the
distance a run is measured at (its nearest column), the tie-break between two
gaps of nearly equal width (the one nearer ahead), and what ``d_min`` is taken
over (the whole arc, not just the passable part -- the wall you are about to
walk into is by definition impassable).
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Callable, Final, Sequence

from cane.bridge.gap_anchor import DepthAnchor
from cane.bridge.gap_ring import COLUMN_WIDTH_DEG, GapRing

#: Plan Sec 3. ``D_STOP_M`` is roughly one cane sweep; Shepherd's 0.2 m is a
#: motor-stopping distance for a wheeled tip and a walking wearer needs
#: telling earlier.
D_STOP_M: Final = 0.60
D_CLEAR_M: Final = 3.00
#: A gap narrower than this is not a gap.
BODY_WIDTH_M: Final = 0.70
#: gapDirection is the centroid bearing normalised by this, so +-90 degrees
#: maps onto +-1 (plan Sec 2.2).
GAP_DIRECTION_SCALE_DEG: Final = 90.0
#: Two gaps within one column of each other are the same width as far as a
#: 3.75-degree ring can tell, and the plan's acceptance is explicit that the
#: nearer-ahead one wins rather than "the wider one by a pixel".
#:
#: Since step 27 the anchored rule scores a run in *free metres* rather than in
#: columns, and the tolerance is read as "one column's worth of the best run's
#: own freedom" -- which reduces exactly to the column rule when every column of
#: both runs is at the same distance, so the scale-free path is unchanged.
WIDTH_TIE_COLUMNS: Final = 1
#: Ties are compared with a relative slack so two runs that differ only in the
#: last bit of a floating-point sum are not silently ordered by rounding.
_TIE_EPS: Final = 1e-9
#: ``proximity`` with no accepted scale anchor -- the plan's own Risks 1
#: contingency, verbatim: "leave proximityFactor at a constant 0.5". Step 22
#: turned that contingency into the normal case. It is a constant and not a
#: ramp over a placeholder scale because a guessed metre dressed as a number
#: is the silent fake step 8 forbids; ``proximity_anchored=false`` travels
#: beside it so the phone treats it as a band (Sec 2.3).
UNANCHORED_PROXIMITY: Final = 0.5
#: A column is passable, with no scale, when it is at least half way up the
#: frame's own depth range measured from the nearest thing in it. In metres
#: that is the *harmonic* mean of the nearest and farthest distance in view --
#: about twice the nearest obstacle's distance when the view is open -- which
#: is a defensible "far enough to walk toward" and, unlike a fixed metric
#: threshold, survives both unknowns of an affine-invariant depth model.
UNANCHORED_PASSABLE_FREEDOM: Final = 0.5


def _clamp(value: float, low: float = 0.0, high: float = 1.0) -> float:
    return max(low, min(high, value))


def is_passable(distance_m: float | None) -> bool:
    """A column clears the stopping distance. Absent evidence never does:
    "we did not see" is not "it is clear" (step 8's no-silent-fake rule).
    """
    return distance_m is not None and distance_m > D_STOP_M


def free_distance_m(distance_m: float | None) -> float:
    """How much room a column has *beyond* the stopping distance, never below 0.

    The weight behind both halves of plan Sec 3.3 ruling 2's fix, and the reason
    it is ``d - D_STOP_M`` rather than ``d``: the weight vanishes exactly where
    passability does, so a column that has only just cleared the stopping
    distance is not somewhere to aim, and a column joining or leaving a run at
    its edge barely moves the direction. Weighting by ``d`` would make a run's
    barely-passable fringe pull as hard as its open middle.
    """
    if distance_m is None:
        return 0.0
    return max(0.0, distance_m - D_STOP_M)


def required_width_deg(distance_m: float) -> float:
    """The angle a ``BODY_WIDTH_M`` body subtends at ``distance_m``."""
    return math.degrees(2.0 * math.atan(BODY_WIDTH_M / (2.0 * distance_m)))


#: The width test with no scale to evaluate it at. Derived, not invented: it
#: is the most permissive width the metric rule could ever ask for -- a body
#: at the far end of the ramp. The near end (a body at ``D_STOP_M``) is over
#: 60 degrees, which would call every doorway blocked on the path that
#: actually runs today.
UNANCHORED_MIN_GAP_DEG: Final = required_width_deg(D_CLEAR_M)


def columns_needed(distance_m: float) -> int:
    """The same test in columns -- computed, never a hardcoded count."""
    return math.ceil(required_width_deg(distance_m) / COLUMN_WIDTH_DEG)


def proximity_factor(d_min_m: float) -> float:
    """Shepherd's ramp: 0 at ``D_CLEAR_M``, 1 at ``D_STOP_M`` and nearer."""
    return _clamp((D_CLEAR_M - d_min_m) / (D_CLEAR_M - D_STOP_M))


def steering_command(gap_direction: float, proximity: float) -> float:
    """``sign(g) * sqrt(|g|) * proximityFactor``, Shepherd stage 5 verbatim."""
    magnitude = math.sqrt(abs(gap_direction)) * proximity
    return _clamp(math.copysign(magnitude, gap_direction), -1.0, 1.0)


@dataclass(frozen=True, slots=True)
class PassableRun:
    """A contiguous stretch of passable columns, ``[start, stop)``.

    ``nearest_m`` is the *smallest* distance inside the run and is ``None`` on
    the unanchored path, where there are no metres. The anchored rule
    evaluates the width test there -- at the run's nearest column -- because
    the angular width a body needs shrinks with distance, so the gap has to be
    wide enough where it is tightest. ``required_deg`` is therefore carried
    rather than recomputed: it is the one number the two rules disagree about.

    Two more numbers the two rules disagree about, both from plan Sec 3.3
    ruling 2:

    * ``direction_bearing_deg`` -- where to actually aim. On the anchored path it
      is the run's **free-distance-weighted** centroid, which is what stops a
      wall 1.2 m dead ahead emitting ``steer = 0.0`` because the binary rule
      called every column passable. On the unanchored path there are no metres to
      weight with, so it *is* ``centroid_bearing_deg``.
    * ``score`` -- how good the run is, for :func:`choose_run`. Free metres on
      the anchored path ("choose the run by free distance, not by column count"),
      column count on the unanchored one.

    ``centroid_bearing_deg`` is kept beside the direction rather than replaced:
    it is the binary rule's answer, and a test asserts it is still exactly 0 in
    ruling 2's scene, which is what shows the fix is the weighting and not a
    different gap.
    """

    start: int
    stop: int
    centroid_bearing_deg: float
    nearest_m: float | None
    required_deg: float
    direction_bearing_deg: float
    score: float

    @property
    def width_columns(self) -> int:
        return self.stop - self.start

    @property
    def width_deg(self) -> float:
        return self.width_columns * COLUMN_WIDTH_DEG

    @property
    def qualifies(self) -> bool:
        return self.width_deg >= self.required_deg


@dataclass(frozen=True, slots=True)
class SteeringSolution:
    """What one ring says, before it becomes a wire frame.

    ``run`` is ``None`` exactly when ``blocked`` is true, and then
    ``gap_bearing_deg`` points at the least-bad column rather than at 0.0
    (plan Sec 3.1 ruling 2). ``d_min_m`` is ``None`` on the unanchored path:
    there is no metre, and reporting one would be the fake this whole module
    is careful about.
    """

    steer: float
    proximity: float
    gap_bearing_deg: float
    blocked: bool
    proximity_anchored: bool
    gap_direction: float
    d_min_m: float | None
    run: PassableRun | None


def column_distances(ring: GapRing, anchor: DepthAnchor) -> tuple[float | None, ...]:
    """Every column in metres, ``None`` where the ring holds no evidence.

    Only meaningful with an accepted anchor. The seam step 23 left for the
    affine fit is this line: each column is converted with **its own crop's**
    fitted pair, because Depth Anything V2's affine freedom is per inference and
    the three forward crops are three inferences. Reading all 48 columns with one
    pair would compare two crops in a third crop's units -- the ring would show a
    wall on whichever side happened to be fitted with the smallest offset.
    """
    return tuple(
        anchor.distance_m(column.depth, column.crop_index) for column in ring.columns
    )


def relative_freedom(ring: GapRing) -> tuple[float | None, ...]:
    """How free each column is, 0 at the nearest thing in view, 1 at the farthest.

    Raw relative depth is inverse depth, so *larger* means nearer. The value
    is ``(v_nearest - v) / (v_nearest - v_farthest)``: a ratio of differences,
    so an affine-invariant model's unknown scale ``a`` and unknown offset
    ``b`` both cancel exactly. That is what makes this a ranking in plan
    Sec 1.1's sense, and why it may drive *direction* while nothing here may
    drive ``proximity``.

    A flat ring -- every column the same depth -- has no ranking in it, so
    every column reads 0.0: nothing is freer than the nearest thing, and the
    caller is meant to conclude ``blocked``. Without a scale, "uniformly far"
    and "uniformly near" are the same picture, and over-blocking is the safe
    way to be wrong.
    """
    depths = [column.depth for column in ring.columns if column.depth is not None]
    if not depths:
        return tuple(None for _ in ring.columns)
    nearest, farthest = max(depths), min(depths)
    span = nearest - farthest
    return tuple(
        None
        if column.depth is None
        else (0.0 if span <= 0.0 else (nearest - column.depth) / span)
        for column in ring.columns
    )


@dataclass(frozen=True, slots=True)
class RunEvidence:
    """What one passability rule contributes about a run it found.

    Split out so :func:`_collect_runs` owns the *shape* of a run (where it starts
    and stops, its plain centroid) and each rule owns its *reading* of it. The
    defaults are the unanchored rule's: no metres, one fixed width, aim at the
    plain centroid, score by column count.
    """

    nearest_m: float | None = None
    required_deg: float = UNANCHORED_MIN_GAP_DEG
    direction_bearing_deg: float | None = None
    score: float | None = None


Describe = Callable[[int, int], RunEvidence]


def weighted_bearing_deg(
    ring: GapRing, weights: Sequence[float], start: int, stop: int
) -> float:
    """``sum(w * bearing) / sum(w)`` over ``[start, stop)``, or the plain mean.

    Plan Sec 3.3 ruling 2: "lean the direction toward the deepest part of the
    chosen run". Falls back to the unweighted centroid when every weight is zero,
    which on the metric path means every column of the run sits exactly at
    ``D_STOP_M`` -- no part of it is deeper than any other, so the plain centroid
    is the honest answer rather than a division by zero.
    """
    total = sum(weights[index] for index in range(start, stop))
    bearings = [ring.columns[index].bearing_deg for index in range(start, stop)]
    if total <= 0.0:
        return sum(bearings) / len(bearings)
    return (
        sum(weights[index] * ring.columns[index].bearing_deg for index in range(start, stop))
        / total
    )


def _collect_runs(
    ring: GapRing, passable: Sequence[bool], describe: Describe
) -> tuple[PassableRun, ...]:
    """Contiguous ``True`` stretches, left to right, described by the caller's rule.

    An absent column is never passable, so a failed crop ends a run rather
    than joining it: a hole in the evidence is not a way through.
    """
    runs: list[PassableRun] = []
    start: int | None = None
    for index in range(len(passable) + 1):
        here = index < len(passable) and passable[index]
        if here and start is None:
            start = index
        elif not here and start is not None:
            evidence = describe(start, index)
            bearings = [ring.columns[i].bearing_deg for i in range(start, index)]
            centroid = sum(bearings) / len(bearings)
            runs.append(
                PassableRun(
                    start=start,
                    stop=index,
                    centroid_bearing_deg=centroid,
                    nearest_m=evidence.nearest_m,
                    required_deg=evidence.required_deg,
                    direction_bearing_deg=(
                        centroid
                        if evidence.direction_bearing_deg is None
                        else evidence.direction_bearing_deg
                    ),
                    score=float(index - start) if evidence.score is None else evidence.score,
                )
            )
            start = None
    return tuple(runs)


def passable_runs(
    ring: GapRing, distances: Sequence[float | None]
) -> tuple[PassableRun, ...]:
    """The metric rule: clears ``D_STOP_M``, a body wide at its nearest column.

    Since step 27 it also reads plan Sec 3.3 ruling 2's two numbers off the same
    distances: the run is scored by the free metres inside it and aimed at their
    centroid.
    """
    free = [free_distance_m(distance) for distance in distances]

    def describe(start: int, stop: int) -> RunEvidence:
        nearest = min(d for d in distances[start:stop] if d is not None)
        return RunEvidence(
            nearest_m=nearest,
            required_deg=required_width_deg(nearest),
            direction_bearing_deg=weighted_bearing_deg(ring, free, start, stop),
            score=sum(free[start:stop]),
        )

    return _collect_runs(ring, [is_passable(d) for d in distances], describe)


def freedom_runs(
    ring: GapRing, freedoms: Sequence[float | None]
) -> tuple[PassableRun, ...]:
    """The scale-free rule: in the freer half of the frame's own depth range.

    Deliberately unchanged by step 27. The weighting ruling 2 asks for is
    *metric* -- it is ``d - D_STOP_M`` -- and there is no ``d`` here; and this
    rule never had ruling 2's defect anyway, because its threshold self-scales to
    the scene instead of sitting at a fixed 0.6 m, so the wall is already outside
    the run. Weighting by ``relative_freedom`` instead would be a third rule
    nobody has measured.
    """
    passable = [
        freedom is not None and freedom >= UNANCHORED_PASSABLE_FREEDOM
        for freedom in freedoms
    ]
    return _collect_runs(ring, passable, lambda start, stop: RunEvidence())


def choose_run(runs: Sequence[PassableRun]) -> PassableRun | None:
    """The freest qualifying run, ties broken toward dead ahead.

    Plan Sec 3.3 ruling 2: the run is chosen by **free distance**, not by column
    count, so a wide shallow gap no longer beats a narrower open one -- which is
    how a wearer got sent into the tighter, nearer opening. ``score`` is free
    metres on the metric path and column count on the scale-free one, and the
    rest of this function is the same for both.

    The tie tolerance is one column's worth of the *best run's own* freedom, so
    two runs whose columns are all at the same distance behave exactly as step 23
    left them: "within ``WIDTH_TIE_COLUMNS`` of the widest". Ties go to the
    smallest absolute bearing, because "widest" alone would send a wearer
    diagonally across a corridor to a gap one column better than the one in front
    of them (plan step 23 acceptance).
    """
    qualifying = tuple(run for run in runs if run.qualifies)
    if not qualifying:
        return None
    best = max(qualifying, key=lambda run: run.score)
    tolerance = WIDTH_TIE_COLUMNS * best.score / best.width_columns
    floor = best.score - tolerance - _TIE_EPS * abs(best.score)
    contenders = [run for run in qualifying if run.score >= floor]
    return min(
        contenders,
        key=lambda run: (abs(run.direction_bearing_deg), -run.score, run.start),
    )


def least_bad_bearing_deg(
    ring: GapRing, ranking: Sequence[float | None]
) -> float | None:
    """The freest column (plan Sec 3.1 ruling 2), by metres or by freedom.

    Either ranking works because both are "larger is freer". Ties go to the
    bearing nearest ahead: with two equally free sides, the smaller turn is
    the cheaper one for a walking wearer.
    """
    candidates = [
        (value, -abs(ring.columns[index].bearing_deg), ring.columns[index].bearing_deg)
        for index, value in enumerate(ranking)
        if value is not None
    ]
    if not candidates:
        return None
    return max(candidates)[2]


def solve_steering(ring: GapRing, anchor: DepthAnchor) -> SteeringSolution | None:
    """One ring -> one steering decision, or ``None`` when there is no evidence.

    ``None`` is not "go straight": a ring with nothing in it means the depth
    head told us nothing, and the caller must then send no frame at all
    rather than a confident zero (plan Sec 4.2).
    """
    if anchor.accepted:
        ranking = column_distances(ring, anchor)
        runs = passable_runs(ring, ranking)
    else:
        ranking = relative_freedom(ring)
        runs = freedom_runs(ring, ranking)
    present = [value for value in ranking if value is not None]
    if not present:
        return None
    run = choose_run(runs)
    bearing = (
        run.direction_bearing_deg
        if run is not None
        else least_bad_bearing_deg(ring, ranking)
    )
    assert bearing is not None  # `present` is non-empty, so a least-bad exists
    gap_direction = _clamp(bearing / GAP_DIRECTION_SCALE_DEG, -1.0, 1.0)
    d_min_m = min(present) if anchor.accepted else None
    proximity = proximity_factor(d_min_m) if d_min_m is not None else UNANCHORED_PROXIMITY
    return SteeringSolution(
        steer=steering_command(gap_direction, proximity),
        proximity=proximity,
        gap_bearing_deg=bearing,
        blocked=run is None,
        proximity_anchored=anchor.accepted,
        gap_direction=gap_direction,
        d_min_m=d_min_m,
        run=run,
    )
