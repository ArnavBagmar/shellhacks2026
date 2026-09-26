"""Passability, the gap, and Shepherd's steering law (step 23 B and C).

Everything here is pure: a ring of 48 distances in, one
:class:`SteeringSolution` out. No pipeline, no socket, no clock -- which is
the point of keeping the law in its own module (step 23 acceptance 8).

The scripted worlds are geometric, not flat: a wall alongside the wearer
subtends ``L / sin|bearing|``, nearest at the edge of the arc. Scripting a
wall as one constant distance would make every left column equidistant and
would prove nothing about a centroid.
"""

from __future__ import annotations

import math
from dataclasses import replace

import pytest

from cane.bridge.gap_anchor import anchor_for_frame
from cane.bridge.gap_ring import (
    COLUMN_WIDTH_DEG,
    RING_COLUMNS,
    GapRing,
    ring_from_profiles,
)
from cane.bridge.steering import (
    BODY_WIDTH_M,
    D_CLEAR_M,
    D_STOP_M,
    GAP_DIRECTION_SCALE_DEG,
    UNANCHORED_MIN_GAP_DEG,
    UNANCHORED_PASSABLE_FREEDOM,
    UNANCHORED_PROXIMITY,
    WIDTH_TIE_COLUMNS,
    relative_freedom,
    column_distances,
    columns_needed,
    free_distance_m,
    is_passable,
    passable_runs,
    proximity_factor,
    required_width_deg,
    solve_steering,
    steering_command,
)
from cane.cloud.api_models import COLUMNS_PER_CROP

from .conftest import (
    FREE_DISTANCE_M,
    YAWS,
    depth_for,
    make_gap_profile,
    make_ring,
    ring_bearings,
    wall_plane_distances,
)

WALL_M = 0.40
GAP_M = 2.0
# The scale cancels out: `make_ring` writes metres through the same constant
# the anchor reads them back with, so these tests never depend on its value.
ANCHOR = anchor_for_frame((make_gap_profile(5, [1.0] * COLUMNS_PER_CROP),), ())
UNANCHORED = anchor_for_frame(
    (make_gap_profile(5, [1.0] * COLUMNS_PER_CROP, anchored=False),), ()
)
CLOSING_WALLS_M = (0.58, 0.50, 0.40, 0.30, 0.20)


def _solve(distances, anchor=ANCHOR):
    solution = solve_steering(make_ring(distances), anchor)
    assert solution is not None
    return solution


def _corridor(gaps: dict[range, float], wall_m: float = WALL_M) -> list[float]:
    distances = [wall_m] * RING_COLUMNS
    for span, distance in gaps.items():
        for index in span:
            distances[index] = distance
    return distances


def test_the_constants_are_the_plans() -> None:
    assert (D_STOP_M, D_CLEAR_M, BODY_WIDTH_M) == (0.60, 3.00, 0.70)
    assert GAP_DIRECTION_SCALE_DEG == 90.0


def test_a_column_is_passable_only_when_it_clears_the_stopping_distance() -> None:
    assert not is_passable(D_STOP_M), "at the stop distance it has not cleared it"
    assert not is_passable(D_STOP_M / 2)
    assert is_passable(D_STOP_M * 1.001)
    assert not is_passable(None)


def test_impassable_columns_cut_the_arc_into_runs() -> None:
    distances = [D_STOP_M * 2] * RING_COLUMNS
    distances[10] = D_STOP_M * 0.9
    distances[11] = D_STOP_M * 0.5

    ring = make_ring(distances)
    runs = passable_runs(ring, column_distances(ring, ANCHOR))

    assert [(run.start, run.stop) for run in runs] == [(0, 10), (12, RING_COLUMNS)]


def test_an_absent_column_breaks_a_run_rather_than_joining_it() -> None:
    distances: list[float | None] = [GAP_M] * RING_COLUMNS
    distances[20] = None

    ring = make_ring(distances)
    runs = passable_runs(ring, column_distances(ring, ANCHOR))

    assert [(run.start, run.stop) for run in runs] == [(0, 20), (21, RING_COLUMNS)]


def test_the_angular_width_a_body_needs_shrinks_with_distance() -> None:
    near = required_width_deg(1.0)
    far = required_width_deg(3.0)

    assert near == pytest.approx(math.degrees(2 * math.atan(BODY_WIDTH_M / 2.0)))
    assert far < near
    # Never a hardcoded column count: the same gap qualifies further away.
    assert columns_needed(3.0) < columns_needed(1.0)
    assert columns_needed(1.0) * COLUMN_WIDTH_DEG >= near


def test_a_left_wall_steers_right_and_a_right_wall_steers_left() -> None:
    left = _solve(wall_plane_distances(WALL_M, side="left"))
    right = _solve(wall_plane_distances(WALL_M, side="right"))

    assert left.steer > 0.0
    assert left.gap_bearing_deg > 0.0
    assert right.steer == pytest.approx(-left.steer)
    assert right.gap_bearing_deg == pytest.approx(-left.gap_bearing_deg)
    assert not left.blocked and not right.blocked


def test_abs_steer_rises_monotonically_as_the_wall_closes() -> None:
    magnitudes = [
        _solve(wall_plane_distances(lateral, side="left")).steer
        for lateral in CLOSING_WALLS_M
    ]

    assert all(value > 0.0 for value in magnitudes)
    assert magnitudes == sorted(magnitudes), magnitudes
    assert len(set(magnitudes)) == len(magnitudes), "a closing wall must move the number"


def test_a_gap_straddling_a_crop_seam_is_one_run_not_two() -> None:
    """The justification for assembling the whole ring before smoothing."""
    seam = COLUMNS_PER_CROP  # ring index 16 is C0's first column
    distances = _corridor({range(seam - 2, seam + 5): GAP_M})

    ring = make_ring(distances)
    runs = passable_runs(ring, column_distances(ring, ANCHOR))

    assert len(runs) == 1
    assert (runs[0].start, runs[0].stop) == (seam - 2, seam + 5)
    assert runs[0].start < seam <= runs[0].stop - 1, "the run must cross the seam"


def test_a_seam_gap_survives_smoothing_and_is_chosen_as_one_gap() -> None:
    """Through the real path: profiles -> assemble -> smooth -> solve. The two
    columns either side of the seam keep their depth (no phantom wall), and
    the gap is one qualifying run, not two halves that each fail the width
    test.
    """
    left = [WALL_M] * 8 + [GAP_M] * 8
    centre = [GAP_M] * 8 + [WALL_M] * 8
    profiles = (
        make_gap_profile(5, [depth_for(d) for d in left]),
        make_gap_profile(0, [depth_for(d) for d in centre]),
        make_gap_profile(1, [depth_for(WALL_M)] * COLUMNS_PER_CROP),
    )
    ring = ring_from_profiles(profiles, YAWS, 0.0)

    runs = passable_runs(ring, column_distances(ring, ANCHOR))
    solution = solve_steering(ring, ANCHOR)

    assert len(runs) == 1
    assert (runs[0].start, runs[0].stop) == (8, COLUMNS_PER_CROP + 8)
    assert runs[0].qualifies
    assert solution is not None and not solution.blocked
    # The seam columns are untouched by the seam itself: both sides were free.
    assert ring.columns[15].depth == pytest.approx(ring.columns[16].depth)


def test_two_qualifying_gaps_pick_the_one_nearer_ahead() -> None:
    """Plan step 23 acceptance: "not the wider one by a pixel"."""
    wide_left = range(6, 16)  # 10 columns, centroid well off to the left
    narrow_ahead = range(24, 33)  # 9 columns, centroid near dead ahead

    solution = _solve(_corridor({wide_left: GAP_M, narrow_ahead: GAP_M}))

    bearings = ring_bearings()
    expected = (bearings[24] + bearings[32]) / 2
    assert solution.gap_bearing_deg == pytest.approx(expected)
    assert solution.steer > 0.0
    assert not solution.blocked


def test_a_gap_wider_by_more_than_the_tie_tolerance_still_wins() -> None:
    """The tie-break is a tolerance, not a rule that always prefers ahead."""
    wide_left = range(4, 16)  # 12 columns
    narrow_ahead = range(24, 33)  # 9 columns: outside WIDTH_TIE_COLUMNS

    solution = _solve(_corridor({wide_left: GAP_M, narrow_ahead: GAP_M}))

    assert WIDTH_TIE_COLUMNS == 1
    assert solution.gap_bearing_deg < 0.0
    assert solution.steer < 0.0


def test_a_gap_narrower_than_a_body_is_not_a_gap() -> None:
    distances = _corridor({range(22, 25): 1.0})

    solution = _solve(distances)

    assert solution.blocked
    assert solution.run is None


def test_when_blocked_the_bearing_points_at_the_least_bad_column() -> None:
    """Plan Sec 3.1 ruling 2: the most free distance even though it failed the
    width test -- never a hardcoded 0.0, which would steer into whatever is
    dead ahead.
    """
    best = 40
    distances = _corridor({range(best, best + 1): 1.5})

    solution = _solve(distances)

    assert solution.blocked
    assert solution.gap_bearing_deg == pytest.approx(ring_bearings()[best])
    assert solution.steer > 0.0, "a blocked wearer still gets a nudge to the best side"


def test_the_proximity_ramp_keeps_shepherds_shape() -> None:
    assert proximity_factor(D_CLEAR_M) == pytest.approx(0.0)
    assert proximity_factor(D_CLEAR_M + 10) == pytest.approx(0.0)
    assert proximity_factor(D_STOP_M) == pytest.approx(1.0)
    assert proximity_factor(D_STOP_M / 2) == pytest.approx(1.0)
    midpoint = (D_CLEAR_M + D_STOP_M) / 2
    assert proximity_factor(midpoint) == pytest.approx(0.5)


def test_proximity_reads_the_nearest_column_in_the_whole_arc() -> None:
    """Not the nearest *passable* one: the wall you are about to hit is by
    definition impassable, and excluding it would make an approach invisible.
    """
    distances = _corridor({range(24, RING_COLUMNS): FREE_DISTANCE_M}, wall_m=1.2)

    solution = _solve(distances)

    assert solution.d_min_m == pytest.approx(1.2)
    assert solution.proximity == pytest.approx(proximity_factor(1.2))


@pytest.mark.parametrize(
    ("gap_direction", "proximity"),
    [(0.0, 1.0), (1.0, 1.0), (-1.0, 1.0), (0.25, 0.5), (-0.64, 0.75), (0.5, 0.0)],
)
def test_the_steering_law_is_sign_sqrt_times_proximity(
    gap_direction: float, proximity: float
) -> None:
    expected = math.copysign(math.sqrt(abs(gap_direction)), gap_direction) * proximity

    assert steering_command(gap_direction, proximity) == pytest.approx(expected)


def test_the_steering_command_stays_inside_the_contract_bounds() -> None:
    assert steering_command(1.0, 1.0) == pytest.approx(1.0)
    assert steering_command(-1.0, 1.0) == pytest.approx(-1.0)


def test_gap_direction_is_the_centroid_over_ninety_degrees() -> None:
    solution = _solve(_corridor({range(24, 33): GAP_M}))

    assert solution.gap_direction == pytest.approx(
        solution.gap_bearing_deg / GAP_DIRECTION_SCALE_DEG
    )
    assert -1.0 <= solution.gap_direction <= 1.0


def test_an_unanchored_frame_reports_one_constant_band_not_a_number() -> None:
    """Sec 2.3 plus the plan's own Risks 1 contingency, which step 22 has now
    triggered for real: the ground fit is rejected on every live crop, so this
    is the path that runs at the demo.

    ``proximity`` may not be read as a metre, and it may not be *derived* from
    a placeholder scale either -- laundering a guessed metre through a band
    would be the silent fake step 8 forbids. It is one named constant, and
    ``proximity_anchored=false`` is what tells the phone so.
    """
    distances = wall_plane_distances(1.2, side="left")

    anchored = _solve(distances)
    banded = _solve(distances, UNANCHORED)

    assert anchored.proximity_anchored and not banded.proximity_anchored
    assert banded.proximity == pytest.approx(UNANCHORED_PROXIMITY)
    assert banded.d_min_m is None, "there is no metre to report"
    # The nearest column of a wall *plane* at 1.2 m is a shade beyond 1.2: it
    # is the outermost column, at 88.125 and not 90 degrees.
    assert anchored.d_min_m == pytest.approx(1.2, rel=1e-3)


def test_an_unanchored_left_wall_still_steers_right() -> None:
    """Direction is a ranking, so it survives an unknown scale (Sec 1.1)."""
    left = _solve(wall_plane_distances(WALL_M, side="left"), UNANCHORED)
    right = _solve(wall_plane_distances(WALL_M, side="right"), UNANCHORED)

    assert left.steer > 0.0
    assert right.steer == pytest.approx(-left.steer)
    assert not left.blocked
    # Half strength, by construction: the direction is evidence, the urgency
    # is not.
    assert abs(left.steer) == pytest.approx(
        math.sqrt(abs(left.gap_direction)) * UNANCHORED_PROXIMITY
    )


def test_the_unanchored_rule_cancels_both_unknowns_of_an_affine_depth_model() -> None:
    """Depth Anything V2 is affine-invariant in disparity: ``v = a/d + b`` with
    both ``a`` and ``b`` unknown per frame (step 22 measured the offset
    dominating, which is why the one-parameter fit failed). ``relative_freedom``
    is a ratio of differences, so ``a`` and ``b`` both cancel -- unlike a fixed
    metric threshold, which is meaningless under an unknown ``b``.
    """
    ring = make_ring(wall_plane_distances(WALL_M, side="left"))
    scaled = GapRing(
        columns=tuple(
            replace(column, depth=None if column.depth is None else 3.0 * column.depth + 7.0)
            for column in ring.columns
        )
    )

    assert relative_freedom(scaled) == pytest.approx(relative_freedom(ring))


def test_an_unanchored_solution_cannot_feel_the_wall_closing() -> None:
    """The honest limit, pinned as a test rather than hidden.

    A scale-free rule is scale-invariant, so a wall plane at 0.5 m in front of
    a 6 m view and the same scene halved look identical. ``abs(steer)``
    therefore cannot rise as the wall closes without an anchor -- which is
    precisely what ``proximity_anchored=false`` is warning the phone about.
    """
    near = _solve(wall_plane_distances(0.30, side="left", free_m=3.0), UNANCHORED)
    far = _solve(wall_plane_distances(0.60, side="left", free_m=6.0), UNANCHORED)

    assert near.steer == pytest.approx(far.steer)
    assert near.gap_bearing_deg == pytest.approx(far.gap_bearing_deg)


def test_the_unanchored_passable_threshold_is_the_harmonic_midpoint() -> None:
    """``freedom >= 0.5`` means "at least the harmonic mean of the nearest and
    farthest distance in view" -- about twice the nearest obstacle's distance
    when the view is open. That is the whole justification for the constant.
    """
    nearest, farthest = 0.5, 6.0
    harmonic = 2.0 / (1.0 / nearest + 1.0 / farthest)
    distances = [farthest] * RING_COLUMNS
    distances[0] = nearest
    distances[1] = harmonic * 0.99
    distances[2] = harmonic * 1.01

    ring = make_ring(distances)
    freedoms = relative_freedom(ring)

    assert freedoms[1] < UNANCHORED_PASSABLE_FREEDOM <= freedoms[2]


def test_an_unanchored_frame_with_no_structure_is_blocked() -> None:
    """A frame whose depth is flat carries no ranking, so there is no gap to
    head for -- and without a scale, "uniformly far" and "uniformly near" are
    the same picture. ``blocked`` means "do not trust this as a path"
    (Sec 3.1 ruling 2's own wording), which is exactly true here. Over-blocking
    is the safe direction; claiming a clear path would not be.
    """
    solution = _solve([FREE_DISTANCE_M] * RING_COLUMNS, UNANCHORED)

    assert solution.blocked
    assert solution.proximity == pytest.approx(UNANCHORED_PROXIMITY)
    assert abs(solution.gap_bearing_deg) <= COLUMN_WIDTH_DEG, "the nudge stays ahead"


def test_the_unanchored_width_test_uses_the_far_end_of_the_ramp() -> None:
    """Without metres there is one fixed width, and it is the most permissive
    the metric rule could ever ask for (a body at ``D_CLEAR_M``). Demanding the
    near-end width -- 60 degrees, a body at ``D_STOP_M`` -- would call every
    doorway blocked on the path that actually runs today.
    """
    assert UNANCHORED_MIN_GAP_DEG == pytest.approx(required_width_deg(D_CLEAR_M))
    assert UNANCHORED_MIN_GAP_DEG < required_width_deg(D_STOP_M)


def test_an_unanchored_narrow_gap_is_still_not_a_gap() -> None:
    distances = _corridor({range(22, 25): GAP_M}, wall_m=0.2)

    wide = _solve(_corridor({range(16, 32): GAP_M}, wall_m=0.2), UNANCHORED)
    narrow = _solve(distances, UNANCHORED)

    assert not wide.blocked
    assert narrow.blocked


def test_a_ring_with_no_evidence_has_nothing_to_say() -> None:
    assert solve_steering(make_ring([None] * RING_COLUMNS), ANCHOR) is None


def test_a_ring_missing_one_crop_still_solves_on_the_arcs_it_has() -> None:
    distances: list[float | None] = list(wall_plane_distances(WALL_M, side="left"))
    for index in range(COLUMNS_PER_CROP, 2 * COLUMNS_PER_CROP):
        distances[index] = None  # C0's depth failed

    solution = _solve(distances)

    assert solution.steer > 0.0, "the free right arc is still the way to go"
    assert solution.run is not None
    assert solution.run.start >= 2 * COLUMNS_PER_CROP


def test_a_surface_at_one_point_two_metres_in_every_direction_has_no_open_side() -> None:
    """The one scene where ``steer = 0.0`` at 1.2 m is the honest answer.

    This was step 23's pin for plan Sec 3.3 ruling 2's defect. Under the
    depth-weighted direction the defect is gone (see
    ``test_a_wall_ahead_at_one_point_two_metres_steers_toward_the_open_side``),
    but a ring that reads 1.2 m in all 48 directions is a *cylinder* around the
    wearer: there is no deepest part to lean toward, so the direction is 0.0
    because the evidence says so, not because the law cannot see the wall.
    ``proximity`` is the half that speaks here, and it reads 0.75.
    """
    solution = _solve([1.2] * RING_COLUMNS)

    assert not solution.blocked
    assert solution.steer == pytest.approx(0.0)
    assert solution.proximity == pytest.approx(0.75)


# --------------------------------------------------------------------------
# Step 27 / plan Sec 3.3 ruling 2: the gap direction is depth-weighted
# --------------------------------------------------------------------------


def _wall_ahead_with_an_opening(
    wall_m: float = 1.2, open_m: float = FREE_DISTANCE_M, open_from: int = 2 * COLUMNS_PER_CROP
) -> list[float]:
    """Ruling 2's scene: a wall across the forward arc at ``wall_m``, with the
    right-hand crop open to ``open_m``. Everything is beyond ``D_STOP_M``, so
    every column is "passable" and the binary rule sees one gap spanning the
    whole arc -- whose centroid is exactly 0.
    """
    return [wall_m] * open_from + [open_m] * (RING_COLUMNS - open_from)


def test_a_wall_ahead_at_one_point_two_metres_steers_toward_the_open_side() -> None:
    """**Plan Sec 3.3 ruling 2's regression test, and step 27's reason to exist.**

    With ``D_STOP_M`` serving as both the passability threshold and the ramp's
    saturation point, nothing at 1.2 m is impassable: the widest passable run is
    the whole arc and its centroid is 0, so step 23 emitted
    ``blocked=false, steer=0.0`` while ``proximity`` already read 0.75. Turning
    metres on without fixing that would have been a regression.

    The direction is now weighted by each column's free distance, so the same
    frame leans into the open side. The run is unchanged -- it is still the whole
    arc, and its *plain* centroid is still 0 -- which is what shows the fix is
    the weighting and not a different gap.
    """
    solution = _solve(_wall_ahead_with_an_opening())

    assert not solution.blocked, "the doorway on the right is walkable"
    assert solution.proximity == pytest.approx(proximity_factor(1.2))
    assert solution.proximity == pytest.approx(0.75)
    assert solution.steer > 0.0, "the open side is the right"
    assert solution.gap_bearing_deg > 0.0
    assert solution.run is not None
    assert (solution.run.start, solution.run.stop) == (0, RING_COLUMNS)
    assert solution.run.centroid_bearing_deg == pytest.approx(0.0), (
        "the binary rule's answer, still 0 -- the weighting is what moved"
    )


def test_the_mirror_of_that_wall_steers_the_other_way_by_exactly_as_much() -> None:
    right_open = _solve(_wall_ahead_with_an_opening())
    left_open = _solve(list(reversed(_wall_ahead_with_an_opening())))

    assert left_open.steer == pytest.approx(-right_open.steer)
    assert left_open.gap_bearing_deg == pytest.approx(-right_open.gap_bearing_deg)


def test_the_weighted_direction_is_the_free_distance_centroid_of_the_run() -> None:
    """The law, stated once: ``sum(w * bearing) / sum(w)`` with
    ``w = d - D_STOP_M``. Weighting by *free* distance rather than by distance
    is what makes the weight vanish exactly where passability does.
    """
    distances = _wall_ahead_with_an_opening()
    bearings = ring_bearings()
    weights = [free_distance_m(d) for d in distances]
    expected = sum(w * b for w, b in zip(weights, bearings)) / sum(weights)

    solution = _solve(distances)

    assert free_distance_m(D_STOP_M) == 0.0
    assert free_distance_m(D_STOP_M / 2) == 0.0
    assert solution.gap_bearing_deg == pytest.approx(expected)


def test_the_weighted_direction_stays_inside_the_run_it_describes() -> None:
    """A weighted mean of the run's own bearings cannot leave the run, so the
    ring's +-90 (camera frame) invariant is untouched by this change.
    """
    for distances in (
        _wall_ahead_with_an_opening(),
        _corridor({range(4, 20): 3.0, range(34, 44): 6.0}),
        wall_plane_distances(WALL_M, side="left"),
        wall_plane_distances(1.2, side="right"),
    ):
        solution = _solve(distances)
        assert solution.run is not None
        span = [
            solution.run.start,
            solution.run.stop - 1,
        ]
        low, high = (ring_bearings()[index] for index in span)
        assert low <= solution.gap_bearing_deg <= high


def test_a_barely_passable_column_carries_almost_no_weight() -> None:
    """Why the weight is ``d - D_STOP_M`` and not ``d``: a column that only just
    clears the stopping distance is not somewhere to aim, so joining or leaving
    a run at its edge moves the direction by almost nothing.
    """
    inside = _corridor({range(20, RING_COLUMNS): 2.0})
    inside[19] = D_STOP_M + 1e-4
    outside = _corridor({range(20, RING_COLUMNS): 2.0})

    joined = _solve(inside)
    apart = _solve(outside)

    assert joined.run is not None and apart.run is not None
    assert (joined.run.start, apart.run.start) == (19, 20)
    assert joined.gap_bearing_deg == pytest.approx(apart.gap_bearing_deg, abs=1e-2)


def test_the_run_is_chosen_by_free_distance_not_by_column_count() -> None:
    """Ruling 2's other half. A wide shallow gap and a narrower deep one: the
    binary rule picks the wide one because it counts columns, which is how a
    wearer gets sent into the tighter, nearer opening.
    """
    wide_and_shallow = range(4, 16)  # 12 columns at 0.9 m
    narrow_and_deep = range(34, 42)  # 8 columns at 6.0 m
    distances = _corridor({wide_and_shallow: 0.9, narrow_and_deep: FREE_DISTANCE_M})

    solution = _solve(distances)

    assert solution.run is not None
    assert (solution.run.start, solution.run.stop) == (34, 42)
    assert solution.steer > 0.0
    # Both runs pass the body-width test, so this is a *choice* and not the
    # safety check doing the work.
    runs = passable_runs(make_ring(distances), column_distances(make_ring(distances), ANCHOR))
    assert [run.qualifies for run in runs] == [True, True]
    assert max(run.width_columns for run in runs) == 12


def test_the_body_width_safety_check_still_outranks_free_distance() -> None:
    """A gap that is deep but too narrow to walk through is still not a gap.
    The depth weighting decides *between* gaps; it never admits one.

    One column is 3.75 degrees and a body at 6 m subtends 6.68, so this is the
    deepest possible gap failing the narrowest possible test. Note that three
    columns at 6 m *would* qualify -- the width test is evaluated at the run's
    own distance, which is step 23 decision 1, not a leak.
    """
    solution = _solve(_corridor({range(23, 24): FREE_DISTANCE_M}))

    assert solution.blocked
    assert solution.run is None
    assert required_width_deg(FREE_DISTANCE_M) > COLUMN_WIDTH_DEG
    assert not _solve(_corridor({range(22, 25): FREE_DISTANCE_M})).blocked


def test_two_gaps_of_equal_free_distance_still_pick_the_one_nearer_ahead() -> None:
    """The tie-break survives the change of currency. With every column of both
    runs at the same distance the score is proportional to the column count, so
    the tolerance reduces exactly to step 23's "within one column of the widest".
    """
    solution = _solve(_corridor({range(6, 16): GAP_M, range(24, 33): GAP_M}))

    bearings = ring_bearings()
    assert solution.gap_bearing_deg == pytest.approx((bearings[24] + bearings[32]) / 2)


# --------------------------------------------------------------------------
# Step 27 acceptance 5: the unanchored path is byte-identical to step 23's
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("lateral_m", "gap_direction", "bearing_deg", "steer"),
    [
        (1.2, 0.2916666666666667, 26.25, 0.27003086243366084),
        (0.40, 0.3125, 28.125, 0.2795084971874737),
    ],
)
def test_the_unanchored_path_reproduces_step_23s_published_numbers(
    lateral_m: float, gap_direction: float, bearing_deg: float, steer: float
) -> None:
    """Acceptance 5, pinned against the numbers step 23 wrote down rather than
    against a re-derivation of them: ``docs/steps/step-23-brief.md`` Sec D cases
    B and D, a left wall plane with a 6 m open right side, `delta_deg = 0`.

    Nothing in step 27 may move these. The unanchored rule is the fallback now,
    not the primary path, but it is still what runs whenever the fit is
    rejected -- which the plan's own Risks 1 says may well be the venue.
    """
    solution = _solve(wall_plane_distances(lateral_m, side="left"), UNANCHORED)

    assert solution.gap_direction == pytest.approx(gap_direction, rel=1e-12)
    assert solution.gap_bearing_deg == pytest.approx(bearing_deg, rel=1e-12)
    assert solution.steer == pytest.approx(steer, rel=1e-12)
    assert solution.proximity == pytest.approx(UNANCHORED_PROXIMITY)
    assert solution.d_min_m is None
    assert not solution.proximity_anchored
    assert not solution.blocked


def test_the_unanchored_path_does_not_weight_by_depth() -> None:
    """The weighting is metric: it is ``d - D_STOP_M``, and without an anchor
    there is no ``d``. So the unanchored run keeps the plain centroid, and
    ruling 2's scene reads exactly as step 23 left it -- which is *also* not the
    defect, because the scale-free threshold self-scales and already cuts that
    wall out of the run.
    """
    solution = _solve(_wall_ahead_with_an_opening(), UNANCHORED)

    assert solution.run is not None
    assert solution.gap_bearing_deg == pytest.approx(solution.run.centroid_bearing_deg)
    assert solution.steer > 0.0


def test_a_solution_is_immutable() -> None:
    solution = _solve([GAP_M] * RING_COLUMNS)

    with pytest.raises((AttributeError, TypeError)):
        solution.steer = 0.5  # type: ignore[misc]
