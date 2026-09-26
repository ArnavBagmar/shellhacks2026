"""One inference response -> one ``steer`` frame, or honestly nothing (step 23 D).

This is the only impure half of step 23: it reads the response's echoed
``t_capture_ms`` (the phone-clock, D-corrected stamp the tick already
carries), decides whether there is anything to say, and asserts the one
invariant the contract is deliberately too wide to catch -- plan Sec 3.3
ruling 1: the ring, in **camera-frame** bearings, within +-90.

The offset tests here are the demo-critical ones. Under plan Sec 3.1 ruling 1
as first built, the invariant was asserted on the rotated ring, and because
the outer column centre is 88.125 degrees *any* mount offset above 1.875
dropped every frame with a warning that read like correct behaviour. A
shoulder-mounted X4 is several degrees off the wearer's axis, so that was
"steering never fires" at the venue. Sec 3.3 ruling 1 moved the invariant
before ``delta_deg`` and bounded the offset instead: no legitimate mount
offset may ever cost a frame.
"""

from __future__ import annotations

import logging
import random

import pytest

from cane.bridge.config import DELTA_DEG_MAX
from cane.bridge.gap_anchor import GROUND_FIT_RESIDUAL_MAX, NO_EVIDENCE_DEPTH
from cane.bridge.gap_ring import DEPTH_CROP_INDICES, RING_COLUMNS
from cane.bridge.steer_frame import (
    CAMERA_BEARING_ABS_MAX_DEG,
    MAX_EMITTED_BEARING_ABS_DEG,
    steer_from_response,
)
from cane.cloud.api_models import COLUMNS_PER_CROP, Detection, InferenceResponse
from cane.contracts.awareness_event import Source
from cane.transport.messages import STEER_TTL_MS, Steer, decode, encode

from .conftest import (
    FREE_DISTANCE_M,
    T_CAPTURE_MS,
    YAWS,
    depth_for,
    make_gap_profile,
    make_response,
    wall_plane_distances,
)

WALL_M = 0.40
RANDOM_RINGS = 200
RANDOM_SEED = 23
MIN_SCRIPTED_M = 0.15
MAX_SCRIPTED_M = 8.0
ABSENT_ODDS = 0.2
#: Two mount offsets a shoulder-worn X4 really has, either side of zero.
#: Both are far outside the +-1.875 the first ruling allowed.
SHOULDER_DELTAS_DEG = (12.0, -25.0)
#: Every offset the config permits, at both ends and either side of the old
#: 1.875 cliff. ``DELTA_DEG_MAX`` itself is included: the widest legal mount.
DELTA_SWEEP_DEG = (
    -DELTA_DEG_MAX,
    -25.0,
    -12.0,
    -1.875,
    0.0,
    1.875,
    12.0,
    25.0,
    DELTA_DEG_MAX,
)
#: A genuine ring-assembly bug, which is what the invariant is *for*: crop 1's
#: yaw swapped with crop 2's, so a forward crop is assembled over +120 and the
#: ring reaches 148 in the camera frame with no mount offset in sight.
BROKEN_YAWS = (0.0, 120.0, 60.0, 180.0, -120.0, -60.0)
#: Nearer than ``D_STOP_M``, and the nearest thing in these scenes: a column
#: neither passability rule calls passable.
NEAR_M = 0.30
#: Half-width of the scripted single gap, in columns. Nine columns wide is
#: ``UNANCHORED_MIN_GAP_DEG`` with room to spare even where the ring's edge
#: clips the window to five, so every position in the sweep has exactly one
#: qualifying run and the gap is the scene's, not a tie-break's.
GAP_HALF_COLUMNS = 4


def _profiles(distances, **kwargs: object) -> tuple:
    """Three profiles from one 48-long list of metres, in crop order."""
    assert len(distances) == RING_COLUMNS
    return tuple(
        make_gap_profile(
            crop_index,
            [
                depth_for(distances[block * COLUMNS_PER_CROP + column])
                for column in range(COLUMNS_PER_CROP)
            ],
            **kwargs,  # type: ignore[arg-type]
        )
        for block, crop_index in enumerate(DEPTH_CROP_INDICES)
    )


def _one_gap_response(centre: int) -> InferenceResponse:
    """A gap ``GAP_HALF_COLUMNS`` either side of ``centre``, walled elsewhere.

    On the **unanchored** path, which is the one that runs live (step 23 C):
    exactly one run qualifies there for every ``centre``, so the emitted
    bearing is that run's centroid and is fixed by the scene. That is what
    lets a sweep over ``delta_deg`` assert an equality rather than a bound.

    On the anchored path it would not be: smoothing leaves the gap's own edge
    columns reading about a metre, the width test is taken at the run's
    nearest column, and a five-column gap then fails it -- so the bearing
    would come from ``least_bad_bearing_deg``, whose tie-break is *wearer*
    frame and legitimately moves with the offset.
    """
    return make_response(_profiles(_one_gap_distances(centre), anchored=False))


def _one_gap_distances(centre: int) -> tuple[float, ...]:
    return tuple(
        FREE_DISTANCE_M if abs(column - centre) <= GAP_HALF_COLUMNS else NEAR_M
        for column in range(RING_COLUMNS)
    )


def _left_wall_response(**kwargs: object) -> InferenceResponse:
    return make_response(_profiles(wall_plane_distances(WALL_M, side="left"), **kwargs))


def _steer(response: InferenceResponse, delta_deg: float = 0.0, **kwargs) -> Steer | None:
    return steer_from_response(response, YAWS, delta_deg, **kwargs)


def test_a_left_wall_becomes_one_steer_frame_that_steers_right() -> None:
    frame = _steer(_left_wall_response())

    assert frame is not None
    assert frame.steer > 0.0
    assert frame.gap_bearing_deg > 0.0
    assert not frame.blocked
    assert frame.proximity_anchored


def test_the_frame_carries_the_ticks_own_capture_stamp_and_the_ttl() -> None:
    """The bridge never reads a clock here: the stamp is the one the cloud
    echoed, which is already phone-clock and D-corrected (step 6/7).
    """
    frame = _steer(_left_wall_response())

    assert frame is not None
    assert frame.t_capture_ms == T_CAPTURE_MS
    assert frame.ttl_ms == STEER_TTL_MS == 400
    assert frame.source == "x4_cloud"


def test_the_frame_round_trips_through_the_wire_codec() -> None:
    frame = _steer(_left_wall_response())
    assert frame is not None

    assert decode(encode(frame)) == frame


def test_the_local_backend_never_produces_a_steer_frame() -> None:
    """Plan Sec 4.2 / step 8's no-silent-fake rule: there is no depth locally.
    A local detector that somehow *did* answer with profiles is still refused,
    so the guard is the source and not merely an empty list.
    """
    assert _steer(_left_wall_response(), source=Source.X4_LOCAL) is None


def test_a_response_with_no_profiles_produces_no_frame() -> None:
    assert _steer(make_response(())) is None


def test_a_response_whose_columns_are_all_absent_produces_no_frame() -> None:
    profiles = tuple(
        make_gap_profile(crop, [NO_EVIDENCE_DEPTH] * COLUMNS_PER_CROP)
        for crop in DEPTH_CROP_INDICES
    )

    assert _steer(make_response(profiles)) is None


def test_a_missing_crop_still_yields_a_frame_for_the_arcs_we_have() -> None:
    profiles = _profiles(wall_plane_distances(WALL_M, side="left"))
    without_centre = tuple(p for p in profiles if p.crop_index != 0)

    frame = _steer(make_response(without_centre))

    assert frame is not None
    assert frame.steer > 0.0


def test_a_rejected_anchor_still_yields_a_usable_frame() -> None:
    frame = _steer(_left_wall_response(ground_fit_residual=GROUND_FIT_RESIDUAL_MAX * 3))

    assert frame is not None
    assert frame.proximity_anchored is False
    assert isinstance(frame.blocked, bool)
    assert abs(frame.gap_bearing_deg) <= CAMERA_BEARING_ABS_MAX_DEG
    assert frame.steer > 0.0


def test_every_camera_frame_bearing_stays_inside_the_forward_arc() -> None:
    """Plan Sec 3.3 ruling 1, as a property over scripted rings. With no mount
    offset the emitted bearing *is* the camera-frame one, so this is the
    invariant itself: the contract allows (-180, 180], the forward ring may
    only ever reach +-90 before ``delta_deg`` is added.
    """
    rng = random.Random(RANDOM_SEED)
    emitted = 0

    for _ in range(RANDOM_RINGS):
        distances = [
            None
            if rng.random() < ABSENT_ODDS
            else rng.uniform(MIN_SCRIPTED_M, MAX_SCRIPTED_M)
            for _ in range(RING_COLUMNS)
        ]
        anchored = rng.random() < 0.5
        frame = _steer(make_response(_profiles(distances, anchored=anchored)))
        if frame is None:
            continue
        emitted += 1
        assert abs(frame.gap_bearing_deg) <= CAMERA_BEARING_ABS_MAX_DEG, distances
        assert -1.0 <= frame.steer <= 1.0
        assert 0.0 <= frame.proximity <= 1.0

    assert emitted > RANDOM_RINGS // 2, "the scripted rings must mostly produce frames"


@pytest.mark.parametrize("delta_deg", SHOULDER_DELTAS_DEG)
def test_a_real_shoulder_mount_offset_costs_no_frame(
    delta_deg: float, caplog: pytest.LogCaptureFixture
) -> None:
    """The demo-critical one (plan Sec 3.3 ruling 1).

    The mount offset is a rigid rotation of the whole arc into the wearer's
    frame, so the honest emitted bearing is the camera-frame one plus
    ``delta_deg`` -- not a dropped frame, and not a bearing clamped to 90,
    which would state a direction the ring never measured.
    """
    response = _left_wall_response()

    with caplog.at_level(logging.WARNING):
        offset = _steer(response, delta_deg=delta_deg)
    straight = _steer(response)

    assert straight is not None
    assert offset is not None
    assert offset.gap_bearing_deg == pytest.approx(straight.gap_bearing_deg + delta_deg)
    assert offset.blocked == straight.blocked
    assert caplog.records == []


def test_the_emitted_bearing_may_leave_the_ninety_degree_arc() -> None:
    """Sec 3.3 ruling 1: the emitted bearing spans +-(90 + |delta_deg|).

    With the gap against the outer edge of the ring and the widest legal
    mount, the wearer-frame bearing is past 90 and that is correct -- the old
    ruling would have thrown this frame away.
    """
    outermost = RING_COLUMNS - 1

    frame = _steer(_one_gap_response(outermost), delta_deg=DELTA_DEG_MAX)

    assert frame is not None
    assert frame.gap_bearing_deg > CAMERA_BEARING_ABS_MAX_DEG
    assert abs(frame.gap_bearing_deg) <= MAX_EMITTED_BEARING_ABS_DEG


def test_a_ring_assembled_outside_the_forward_arc_is_suppressed_not_sent(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The invariant still catches a genuine ring-assembly bug.

    A wrong yaw table puts a forward crop's columns at +148 in the **camera**
    frame, with no mount offset involved. That is the bug class Sec 3.3
    ruling 1 keeps the check for: the ring is not the arc the law assumes, the
    emitted centroid would look perfectly legal, and dropping it loudly is the
    only honest answer.
    """
    with caplog.at_level(logging.WARNING):
        frame = steer_from_response(_left_wall_response(), BROKEN_YAWS, 0.0)

    assert frame is None
    assert any("90" in record.getMessage() for record in caplog.records)
    assert any("camera" in record.getMessage() for record in caplog.records)


def test_a_mount_offset_never_decides_whether_a_frame_is_sent() -> None:
    """Sec 3.3 ruling 1's rule, as a property: over the same scripted rings,
    a random legal offset emits exactly the frames a zero offset does.

    Subtracting the offset back out is a legitimate check here precisely
    because it cannot pass if anything folded: a bearing that wrapped past
    +-180 lands 360 degrees from the camera-frame arc.
    """
    rng = random.Random(RANDOM_SEED)
    emitted = 0

    for _ in range(RANDOM_RINGS):
        distances = [
            None
            if rng.random() < ABSENT_ODDS
            else rng.uniform(MIN_SCRIPTED_M, MAX_SCRIPTED_M)
            for _ in range(RING_COLUMNS)
        ]
        response = make_response(_profiles(distances, anchored=rng.random() < 0.5))
        delta_deg = rng.uniform(-DELTA_DEG_MAX, DELTA_DEG_MAX)

        straight = _steer(response)
        offset = _steer(response, delta_deg=delta_deg)

        assert (straight is None) == (offset is None), (delta_deg, distances)
        if offset is None:
            continue
        emitted += 1
        assert abs(offset.gap_bearing_deg - delta_deg) <= CAMERA_BEARING_ABS_MAX_DEG
        assert abs(offset.gap_bearing_deg) <= MAX_EMITTED_BEARING_ABS_DEG
        assert -1.0 <= offset.steer <= 1.0
        assert 0.0 <= offset.proximity <= 1.0

    assert emitted > RANDOM_RINGS // 2, "the scripted rings must mostly produce frames"


def test_no_legal_mount_offset_can_fold_a_bearing_or_flip_its_side() -> None:
    """Sec 3.3 ruling 1's structural claim, swept: every legal offset crossed
    with a gap at every position across the forward arc.

    ``normalize_bearing_deg`` folds anything past +180 back to a *negative*
    bearing, which would put the gap on the wrong side of the wearer -- the
    real hazard behind the +-90 rule. Bounding ``|delta_deg|`` at 30 caps the
    widest bearing at 120, so the fold is unreachable rather than guarded; the
    rotation stays rigid, which is what the equality asserts.
    """
    widest = 0.0

    for centre in range(RING_COLUMNS):
        response = _one_gap_response(centre)
        straight = _steer(response)
        assert straight is not None, centre

        for delta_deg in DELTA_SWEEP_DEG:
            frame = _steer(response, delta_deg=delta_deg)

            assert frame is not None, (centre, delta_deg)
            assert -180.0 < frame.gap_bearing_deg <= 180.0, (centre, delta_deg)
            assert abs(frame.gap_bearing_deg) <= MAX_EMITTED_BEARING_ABS_DEG
            assert frame.gap_bearing_deg == pytest.approx(
                straight.gap_bearing_deg + delta_deg
            ), (centre, delta_deg)
            widest = max(widest, abs(frame.gap_bearing_deg))

    # Not a vacuous sweep: it has to leave the old +-90 ceiling behind, or it
    # would pass just as well against the ruling this fix replaced.
    assert widest > CAMERA_BEARING_ABS_MAX_DEG


def test_a_clear_corridor_goes_quiet_without_claiming_to_be_blocked() -> None:
    frame = _steer(make_response(_profiles([FREE_DISTANCE_M] * RING_COLUMNS)))

    assert frame is not None
    assert frame.steer == pytest.approx(0.0)
    assert frame.proximity == pytest.approx(0.0)
    assert not frame.blocked


# --------------------------------------------------------------------------
# Step 27, end to end: a fitted affine pair on the wire -> metres in the frame
# --------------------------------------------------------------------------

#: The pairs measured on the three forward crops of the real X4 equirect
#: (plan Sec 2.5). Keyed by crop so the conversion is exercised the way it runs:
#: each crop in its own units.
MEASURED_PAIRS: dict[int, tuple[float, float]] = {
    5: (3.658, 2.509),
    0: (0.905, 3.519),
    1: (1.373, 3.810),
}
#: One pair for all three crops, for the scenes that want the ring to read back
#: at exactly the metres it was scripted in across the seams too.
ONE_PAIR: dict[int, tuple[float, float]] = dict.fromkeys(DEPTH_CROP_INDICES, (3.658, 2.509))


def _affine_response(
    distances, pairs: dict[int, tuple[float, float]] = ONE_PAIR, **kwargs: object
) -> InferenceResponse:
    """A response whose columns are real affine depths, `a/d + b`, per crop.

    Round-tripped through the wire so the test proves the *contract* carries
    what the bridge needs, not merely that two python objects agree.
    """
    assert len(distances) == RING_COLUMNS
    profiles = []
    for block, crop_index in enumerate(DEPTH_CROP_INDICES):
        scale, offset = pairs[crop_index]
        profiles.append(
            make_gap_profile(
                crop_index,
                [
                    scale / distances[block * COLUMNS_PER_CROP + column] + offset
                    for column in range(COLUMNS_PER_CROP)
                ],
                ground_scale=scale,
                ground_offset=offset,
                **kwargs,  # type: ignore[arg-type]
            )
        )
    response = make_response(tuple(profiles))
    return InferenceResponse.model_validate_json(response.model_dump_json(by_alias=True))


def test_a_fitted_affine_pair_on_the_wire_anchors_the_frame() -> None:
    """The contract gap, closed. Step 22 could fit a scale and step 23 could use
    one, and neither could reach the other: `GapProfile` had no field for it.
    """
    frame = _steer(_affine_response([2.0] * RING_COLUMNS, MEASURED_PAIRS))

    assert frame is not None
    assert frame.proximity_anchored is True
    assert frame.proximity == pytest.approx(1.0 - (2.0 - 0.6) / (3.0 - 0.6), abs=0.02)


def test_proximity_is_now_a_measurement_and_moves_with_the_distance() -> None:
    """Step 27 acceptance 4. Before this step `proximity` was the named constant
    `UNANCHORED_PROXIMITY`, identical for a 6 m corridor and a 0.3 m wall.
    """
    proximities = [
        _steer(_affine_response([distance] * RING_COLUMNS)).proximity  # type: ignore[union-attr]
        for distance in (3.0, 2.4, 1.8, 1.2, 0.6)
    ]

    assert proximities == pytest.approx([0.0, 0.25, 0.5, 0.75, 1.0], abs=1e-6)
    assert len(set(proximities)) == 5


def test_abs_steer_rises_as_a_wall_closes_in_metres_not_in_arbitrary_units() -> None:
    """Step 27 acceptance 4's second half, end to end through the wire. Step 23
    pinned the *opposite* on the live path: a scale-free rule cannot feel a wall
    closing, because halving every distance is the same picture.
    """
    magnitudes = [
        abs(_steer(_affine_response(wall_plane_distances(lateral, side="left"))).steer)  # type: ignore[union-attr]
        for lateral in (2.0, 1.2, 0.8, 0.5, 0.3)
    ]

    assert magnitudes == sorted(magnitudes), magnitudes
    assert len(set(magnitudes)) == len(magnitudes)


def test_the_ruling_two_wall_emits_a_non_zero_steer_over_the_wire() -> None:
    """Plan Sec 3.3 ruling 2, end to end. A wall across the arc at 1.2 m with
    the right-hand crop open: step 23 emitted `steer 0.0, blocked false` here.
    """
    distances = [1.2] * (2 * COLUMNS_PER_CROP) + [FREE_DISTANCE_M] * COLUMNS_PER_CROP

    frame = _steer(_affine_response(distances))

    assert frame is not None
    assert frame.proximity_anchored is True
    assert frame.proximity == pytest.approx(0.75, abs=0.02)
    assert frame.steer > 0.2, frame
    assert frame.gap_bearing_deg > 0.0
    assert not frame.blocked


def test_an_old_cloud_with_no_scale_falls_back_to_the_unanchored_path() -> None:
    """Step 27 acceptance 6. A response from before this step parses, yields a
    frame, and is honest about having no metres -- it does not borrow the
    placeholder constant to look anchored.
    """
    distances = wall_plane_distances(WALL_M, side="left")
    old = make_response(_profiles(distances, ground_scale=None, ground_offset=None))
    wire = InferenceResponse.model_validate_json(old.model_dump_json(by_alias=True))

    frame = _steer(wire)

    assert wire.gap_profiles[0].ground_scale is None
    assert frame is not None
    assert frame.proximity_anchored is False
    assert frame.proximity == pytest.approx(0.5)
    assert frame.steer > 0.0, "direction is a ranking and survives with no scale"


#: The two columns either side of each of the two crop seams.
SEAM_COLUMNS = (COLUMNS_PER_CROP - 1, COLUMNS_PER_CROP, 2 * COLUMNS_PER_CROP - 1, 2 * COLUMNS_PER_CROP)


def _distances(profiles, anchor) -> tuple[float | None, ...]:
    from cane.bridge.gap_ring import ring_from_profiles
    from cane.bridge.steering import column_distances

    return column_distances(ring_from_profiles(profiles, YAWS, 0.0), anchor)


def test_the_metric_ring_is_one_space_across_the_crop_seams() -> None:
    """The defect the per-crop conversion had to fix, with its before and after.

    `SMOOTH_KERNEL` runs across the crop seams on purpose (that is what makes a
    gap straddling a seam one gap), but each crop is its own inference with its
    own `(a, b)` -- so smoothing **raw** depth there averaged two different unit
    systems. On a scene at a uniform 3 m with the real measured pairs a seam
    column came back as 2.03 m, and since `d_min` is taken over the whole arc it
    set `proximity` for the entire frame.

    Converting each crop with its own pair *before* assembly makes all 48 columns
    inverse metres, so the kernel averages like with like and a uniform scene is
    uniform to the float.
    """
    from cane.bridge.gap_anchor import anchor_for_frame, inverse_metre_anchor, to_inverse_metres

    response = _affine_response([3.0] * RING_COLUMNS, MEASURED_PAIRS)
    anchor = anchor_for_frame(response.gap_profiles, ())

    naive = _distances(response.gap_profiles, anchor)
    fixed = _distances(to_inverse_metres(response.gap_profiles, anchor), inverse_metre_anchor(anchor))

    assert fixed == pytest.approx([3.0] * RING_COLUMNS, rel=1e-9)
    # The same ring without the conversion: only the seams miss, and they miss
    # by a third of the distance.
    off_seam = [d for i, d in enumerate(naive) if i not in SEAM_COLUMNS]
    assert off_seam == pytest.approx([3.0] * len(off_seam), rel=1e-6)
    assert max(abs(naive[i] - 3.0) for i in SEAM_COLUMNS) == pytest.approx(0.97, abs=0.02)  # type: ignore[operator]


def test_the_conversion_leaves_the_absence_and_horizon_conventions_intact() -> None:
    """Two things the remap must not quietly change: a column with no evidence
    stays absent, and a column past the model's horizon stays *far* rather than
    becoming a zero that would read as absence.
    """
    from cane.bridge.gap_anchor import (
        DISTANCE_MAX_M,
        anchor_for_frame,
        inverse_metre_anchor,
        to_inverse_metres,
    )

    scale, offset = MEASURED_PAIRS[5]
    columns = [scale / 2.0 + offset] * COLUMNS_PER_CROP
    columns[0] = NO_EVIDENCE_DEPTH
    columns[1] = offset - 0.5  # beyond the horizon: the sky
    profiles = (
        make_gap_profile(5, columns, ground_scale=scale, ground_offset=offset),
    )
    anchor = anchor_for_frame(profiles, ())

    converted = to_inverse_metres(profiles, anchor)

    assert converted[0].columns[0] == NO_EVIDENCE_DEPTH
    assert converted[0].columns[1] == pytest.approx(1.0 / DISTANCE_MAX_M)
    assert converted[0].columns[2] == pytest.approx(0.5)
    # Absence survives assembly; the sky column is then *smoothed* toward its
    # 2 m neighbour, which is the kernel working, not the conversion leaking.
    distances = _distances(converted, inverse_metre_anchor(anchor))
    assert distances[0] is None
    assert distances[1] is not None and 2.0 < distances[1] < DISTANCE_MAX_M


def test_a_far_detection_over_near_depth_costs_the_anchor_not_the_frame() -> None:
    """The Sec 2.3 cross-check, end to end."""
    far_box = Detection(
        crop_index=5,
        cx=0.5,
        cy=0.5,
        w=0.2,
        h=0.2,
        cls="wall",
        confidence=0.6,
        track_id="x:1",
        range_band="far",
    )
    response = make_response(
        _profiles(wall_plane_distances(WALL_M, side="left")), (far_box,)
    )

    frame = _steer(response)

    assert frame is not None
    assert frame.proximity_anchored is False
