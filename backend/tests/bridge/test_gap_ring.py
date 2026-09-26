"""The 48-column ring: assembly in torso bearings, then smoothing (step 23 A).

Two properties matter more than the arithmetic. First, a ring column's
bearing must be composed exactly the way a *detection's* bearing is
composed (``events.bearing_for_detection``), or the wearer would be told
that the gap and the cyclist are in different places when they are in the
same place. Second, the kernel runs over the assembled ring and therefore
*across* the crop seams: smoothing each crop on its own would taper its
edge columns toward nothing and invent a wall at -30 and +30 degrees, which
is the one artefact this whole step is built to avoid (plan Sec 0 row 4).
"""

from __future__ import annotations

import logging

import pytest

from cane.bridge.dewarp import CROP_FOV_DEG, CROP_YAWS_DEG
from cane.bridge.events import bearing_for_detection
from cane.bridge.gap_ring import (
    COLUMN_WIDTH_DEG,
    DEPTH_CROP_INDICES,
    RING_ARC_DEG,
    RING_COLUMNS,
    SMOOTH_KERNEL,
    assemble_ring,
    column_bearing_deg,
    column_cx,
    column_index_for_cx,
    ring_from_profiles,
    smooth_ring,
)
from cane.cloud.api_models import COLUMNS_PER_CROP, Detection

from .conftest import YAWS, make_gap_profile

NEAR_DEPTH = 1.5
FAR_DEPTH = 0.45
DELTA_DEG = 4.0
FIRST_SEAM = COLUMNS_PER_CROP - 1  # ring index 15: C5's last column
REAR_CROP = 3


def _flat_profiles(value: float) -> tuple:
    return tuple(
        make_gap_profile(crop, [value] * COLUMNS_PER_CROP) for crop in DEPTH_CROP_INDICES
    )


def test_the_ring_is_three_crops_of_sixteen_columns() -> None:
    assert DEPTH_CROP_INDICES == (5, 0, 1)
    assert RING_COLUMNS == len(DEPTH_CROP_INDICES) * COLUMNS_PER_CROP == 48
    assert COLUMN_WIDTH_DEG == pytest.approx(CROP_FOV_DEG / COLUMNS_PER_CROP)
    assert COLUMN_WIDTH_DEG == pytest.approx(3.75)
    assert RING_ARC_DEG == pytest.approx(180.0)


def test_the_bearings_are_contiguous_and_span_the_forward_arc() -> None:
    ring = assemble_ring(_flat_profiles(NEAR_DEPTH), YAWS, 0.0)

    bearings = [column.bearing_deg for column in ring.columns]
    assert bearings == sorted(bearings), "the ring must read left to right"
    steps = [b - a for a, b in zip(bearings, bearings[1:], strict=False)]
    # Contiguous across the seams too: no gap and no overlap at +-30 degrees.
    assert all(step == pytest.approx(COLUMN_WIDTH_DEG) for step in steps)
    assert bearings[0] == pytest.approx(-RING_ARC_DEG / 2 + COLUMN_WIDTH_DEG / 2)
    assert bearings[-1] == pytest.approx(RING_ARC_DEG / 2 - COLUMN_WIDTH_DEG / 2)
    assert all(abs(b) < RING_ARC_DEG / 2 for b in bearings)


@pytest.mark.parametrize("crop_index", DEPTH_CROP_INDICES)
@pytest.mark.parametrize("column", [0, 7, COLUMNS_PER_CROP - 1])
def test_a_column_bearing_is_composed_exactly_like_a_detection_bearing(
    crop_index: int, column: int
) -> None:
    """The one shared line of geometry: ``yaw + (cx - 0.5) * FOV + delta``."""
    detection = Detection(
        crop_index=crop_index,
        cx=column_cx(column),
        cy=0.5,
        w=0.1,
        h=0.1,
        cls="wall",
        confidence=0.5,
        track_id="ring:1",
    )

    assert column_bearing_deg(crop_index, column, YAWS, DELTA_DEG) == pytest.approx(
        bearing_for_detection(detection, YAWS, DELTA_DEG)
    )


def test_the_mount_offset_rotates_every_column() -> None:
    plain = assemble_ring(_flat_profiles(NEAR_DEPTH), CROP_YAWS_DEG, 0.0)
    offset = assemble_ring(_flat_profiles(NEAR_DEPTH), CROP_YAWS_DEG, DELTA_DEG)

    shifts = [
        b.bearing_deg - a.bearing_deg
        for a, b in zip(plain.columns, offset.columns, strict=True)
    ]
    assert all(shift == pytest.approx(DELTA_DEG) for shift in shifts)


def test_profiles_are_indexed_by_crop_index_not_by_list_order() -> None:
    """Plan Sec 3.1 ruling 3: the list may arrive in any order."""
    left = make_gap_profile(5, [NEAR_DEPTH] * COLUMNS_PER_CROP)
    centre = make_gap_profile(0, [FAR_DEPTH] * COLUMNS_PER_CROP)
    right = make_gap_profile(1, [FAR_DEPTH] * COLUMNS_PER_CROP)

    shuffled = assemble_ring((right, left, centre), YAWS, 0.0)
    sorted_order = assemble_ring((left, centre, right), YAWS, 0.0)

    assert shuffled == sorted_order
    assert [c.crop_index for c in shuffled.columns[:COLUMNS_PER_CROP]] == [5] * 16
    assert shuffled.columns[0].depth == pytest.approx(NEAR_DEPTH)
    assert shuffled.columns[-1].depth == pytest.approx(FAR_DEPTH)


def test_a_missing_crop_leaves_its_arc_absent_not_zero() -> None:
    """Ruling 3 again: depth failed for C0, so the middle arc has no evidence.
    Zero would read as "infinitely near" once inverted, i.e. a phantom wall.
    """
    ring = assemble_ring(
        (
            make_gap_profile(5, [NEAR_DEPTH] * COLUMNS_PER_CROP),
            make_gap_profile(1, [FAR_DEPTH] * COLUMNS_PER_CROP),
        ),
        YAWS,
        0.0,
    )

    middle = ring.columns[COLUMNS_PER_CROP : 2 * COLUMNS_PER_CROP]
    assert all(column.depth is None for column in middle)
    assert all(column.depth is not None for column in ring.columns[:COLUMNS_PER_CROP])
    assert ring.crop_indices == frozenset({5, 1})
    assert ring.has_evidence


def test_a_non_positive_column_is_absent_evidence() -> None:
    columns = [NEAR_DEPTH] * COLUMNS_PER_CROP
    columns[3] = None  # written as a non-positive raw depth on the wire

    ring = assemble_ring((make_gap_profile(5, columns),), YAWS, 0.0)

    assert ring.columns[3].depth is None
    assert ring.columns[2].depth is not None


def test_a_rear_crop_profile_is_ignored_with_a_warning(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Depth only runs forward (plan Sec 1.2). A profile for C3 is either an
    older/newer cloud or a producer bug; either way it is not steerable and
    must not silently land in some forward column.
    """
    with caplog.at_level(logging.WARNING):
        ring = assemble_ring(
            (
                make_gap_profile(REAR_CROP, [NEAR_DEPTH] * COLUMNS_PER_CROP),
                make_gap_profile(0, [FAR_DEPTH] * COLUMNS_PER_CROP),
            ),
            YAWS,
            0.0,
        )

    assert ring.crop_indices == frozenset({0})
    assert any(str(REAR_CROP) in record.getMessage() for record in caplog.records)


def test_a_duplicate_crop_index_keeps_the_first_and_warns(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Step 22 validates uniqueness, so this is a broken producer -- but the
    bridge still has to answer, and "first wins, loudly" beats a silent
    overwrite by whichever profile happened to be last in the list.
    """
    with caplog.at_level(logging.WARNING):
        ring = assemble_ring(
            (
                make_gap_profile(0, [NEAR_DEPTH] * COLUMNS_PER_CROP),
                make_gap_profile(0, [FAR_DEPTH] * COLUMNS_PER_CROP),
            ),
            YAWS,
            0.0,
        )

    assert ring.columns[COLUMNS_PER_CROP].depth == pytest.approx(NEAR_DEPTH)
    assert any("duplicate" in record.getMessage().lower() for record in caplog.records)


def test_smoothing_is_shepherds_kernel_on_the_interior() -> None:
    assert SMOOTH_KERNEL == (0.25, 0.5, 0.25)
    columns = [FAR_DEPTH] * COLUMNS_PER_CROP
    columns[8] = NEAR_DEPTH

    ring = smooth_ring(assemble_ring((make_gap_profile(0, columns),), YAWS, 0.0))
    smoothed = ring.columns[COLUMNS_PER_CROP + 8].depth
    neighbour = ring.columns[COLUMNS_PER_CROP + 7].depth

    assert smoothed == pytest.approx(0.5 * NEAR_DEPTH + 0.5 * FAR_DEPTH)
    assert neighbour == pytest.approx(0.25 * NEAR_DEPTH + 0.75 * FAR_DEPTH)


def test_smoothing_runs_across_the_crop_seam() -> None:
    """The seam columns must see each other. C5 is uniformly near and C0 is
    uniformly far, so if the kernel stopped at the crop edge the last C5
    column would be untouched; smoothed across, it carries a quarter of C0.
    """
    ring = smooth_ring(
        assemble_ring(
            (
                make_gap_profile(5, [NEAR_DEPTH] * COLUMNS_PER_CROP),
                make_gap_profile(0, [FAR_DEPTH] * COLUMNS_PER_CROP),
                make_gap_profile(1, [FAR_DEPTH] * COLUMNS_PER_CROP),
            ),
            YAWS,
            0.0,
        )
    )

    last_left = ring.columns[FIRST_SEAM].depth
    first_centre = ring.columns[FIRST_SEAM + 1].depth
    assert last_left == pytest.approx(0.75 * NEAR_DEPTH + 0.25 * FAR_DEPTH)
    assert first_centre == pytest.approx(0.25 * NEAR_DEPTH + 0.75 * FAR_DEPTH)
    # Deeper inside each crop the value is untouched: the seam is a local blend.
    assert ring.columns[0].depth == pytest.approx(NEAR_DEPTH)
    assert ring.columns[-1].depth == pytest.approx(FAR_DEPTH)


def test_smoothing_renormalises_at_the_edge_of_the_evidence() -> None:
    """The arc is not a circle: C5's outer edge and C1's outer edge have no
    neighbour (the rear crops carry no depth), and neither does a column
    beside a failed crop. Padding with zero there would invent a near wall,
    so the kernel is renormalised over the taps that exist.
    """
    ring = smooth_ring(
        assemble_ring((make_gap_profile(5, [NEAR_DEPTH] * COLUMNS_PER_CROP),), YAWS, 0.0)
    )

    assert ring.columns[0].depth == pytest.approx(NEAR_DEPTH)
    assert ring.columns[FIRST_SEAM].depth == pytest.approx(NEAR_DEPTH)
    assert all(column.depth is None for column in ring.columns[COLUMNS_PER_CROP:])


def test_an_absent_column_stays_absent_after_smoothing() -> None:
    columns = [NEAR_DEPTH] * COLUMNS_PER_CROP
    columns[5] = None

    ring = smooth_ring(assemble_ring((make_gap_profile(0, columns),), YAWS, 0.0))

    assert ring.columns[COLUMNS_PER_CROP + 5].depth is None


def test_ring_from_profiles_assembles_then_smooths() -> None:
    profiles = (
        make_gap_profile(5, [NEAR_DEPTH] * COLUMNS_PER_CROP),
        make_gap_profile(0, [FAR_DEPTH] * COLUMNS_PER_CROP),
    )

    assert ring_from_profiles(profiles, YAWS, 0.0) == smooth_ring(
        assemble_ring(profiles, YAWS, 0.0)
    )


def test_no_profiles_at_all_is_an_empty_ring() -> None:
    ring = ring_from_profiles((), YAWS, 0.0)

    assert len(ring.columns) == RING_COLUMNS
    assert not ring.has_evidence
    assert ring.crop_indices == frozenset()


@pytest.mark.parametrize(
    ("cx", "expected"),
    [(0.0, 0), (0.03, 0), (0.5, COLUMNS_PER_CROP // 2), (0.999, COLUMNS_PER_CROP - 1),
     (1.0, COLUMNS_PER_CROP - 1)],
)
def test_a_detection_cx_maps_onto_its_column(cx: float, expected: int) -> None:
    """Used by the Sec 2.3 cross-check: which column is that detection in."""
    assert column_index_for_cx(cx) == expected
