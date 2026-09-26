"""The plan's acknowledged soft spot: relative depth -> metres (step 23 B, plan Sec 2.3).

Two guards are specified and both are tested here: a ground fit the producer
would not stand behind (``anchored``/``ground_fit_residual``) and a fit that
contradicts step 7's pinhole ``range_band`` for a detection in the same crop
by more than one band. A rejected anchor does not stop the frame -- direction
is a ranking and survives (plan Sec 1.1) -- it only stops ``proximity`` being
read as a metre.

The scale itself is a placeholder, and deliberately visible as one: the
contract carries ``anchored`` and a residual but **no fitted scale**, so the
bridge cannot recover metres from the wire at all. See
``docs/steps/step-23-brief.md``; the tests below pin the shape of the
conversion so that swapping the constant for a transmitted scale is a
one-line change.
"""

from __future__ import annotations

import pytest

from cane.bridge.gap_anchor import (
    DISTANCE_MAX_M,
    GROUND_FIT_RESIDUAL_MAX,
    MAX_BAND_DISAGREEMENT,
    NO_EVIDENCE_DEPTH,
    RELATIVE_DEPTH_SCALE_M,
    AnchorVerdict,
    DepthAnchor,
    anchor_for_frame,
    band_distance,
    depth_for_distance_m,
    distance_for_depth,
)
from cane.cloud.api_models import COLUMNS_PER_CROP, Detection
from cane.contracts.awareness_event import RangeBand

from .conftest import make_gap_profile

WALL_M = 1.2
FAR_M = 4.0


def _detection(crop_index: int, cx: float, band: str | None) -> Detection:
    return Detection(
        crop_index=crop_index,
        cx=cx,
        cy=0.5,
        w=0.2,
        h=0.4,
        cls="wall",
        confidence=0.8,
        track_id=f"anchor:{crop_index}",
        range_band=band,
    )


def _profiles(distance_m: float = WALL_M, **kwargs: object) -> tuple:
    columns = [depth_for_distance_m(distance_m)] * COLUMNS_PER_CROP
    return (make_gap_profile(5, columns, **kwargs),)  # type: ignore[arg-type]


def test_distance_is_the_scale_over_the_raw_relative_depth() -> None:
    depth = depth_for_distance_m(WALL_M)

    assert depth == pytest.approx(RELATIVE_DEPTH_SCALE_M / WALL_M)
    assert distance_for_depth(depth) == pytest.approx(WALL_M)


def test_a_non_positive_depth_is_no_evidence_not_a_near_wall() -> None:
    assert NO_EVIDENCE_DEPTH <= 0.0
    assert distance_for_depth(NO_EVIDENCE_DEPTH) is None
    assert distance_for_depth(-2.0) is None
    assert distance_for_depth(None) is None


def test_a_vanishing_depth_saturates_rather_than_returning_infinity() -> None:
    assert distance_for_depth(1e-9) == pytest.approx(DISTANCE_MAX_M)


def test_an_accepted_anchor_carries_the_scale_and_converts() -> None:
    anchor = anchor_for_frame(_profiles(), ())

    assert anchor.verdict is AnchorVerdict.ACCEPTED
    assert anchor.accepted
    assert anchor.scale_m == pytest.approx(RELATIVE_DEPTH_SCALE_M)
    assert anchor.distance_m(depth_for_distance_m(FAR_M)) == pytest.approx(FAR_M)


def test_a_producer_that_will_not_stand_behind_its_fit_rejects_the_frame() -> None:
    anchor = anchor_for_frame(_profiles(anchored=False), ())

    assert anchor.verdict is AnchorVerdict.PRODUCER_UNANCHORED
    assert not anchor.accepted
    # Still converts: direction is a ranking and must survive (Sec 1.1).
    assert anchor.distance_m(depth_for_distance_m(FAR_M)) == pytest.approx(FAR_M)


def test_a_high_ground_fit_residual_rejects_the_frame() -> None:
    anchor = anchor_for_frame(
        _profiles(ground_fit_residual=GROUND_FIT_RESIDUAL_MAX * 2), ()
    )

    assert anchor.verdict is AnchorVerdict.RESIDUAL_TOO_HIGH


def test_one_bad_crop_rejects_the_whole_frames_anchor() -> None:
    """``d_min`` is taken across the whole arc, so a per-crop anchor would
    mix a trusted metre with an untrusted one inside one number. Sec 2.3
    speaks of "the frame's anchor"; this is that reading, made explicit.
    """
    good = make_gap_profile(5, [depth_for_distance_m(WALL_M)] * COLUMNS_PER_CROP)
    bad = make_gap_profile(
        0, [depth_for_distance_m(FAR_M)] * COLUMNS_PER_CROP, anchored=False
    )

    assert not anchor_for_frame((good, bad), ()).accepted


def test_no_profiles_means_there_is_nothing_to_anchor() -> None:
    assert anchor_for_frame((), ()).verdict is AnchorVerdict.NO_PROFILE


def test_the_band_order_is_the_contracts() -> None:
    assert band_distance(RangeBand.NEAR, RangeBand.NEAR) == 0
    assert band_distance(RangeBand.NEAR, RangeBand.MID) == 1
    assert band_distance(RangeBand.NEAR, RangeBand.FAR) == 2
    assert MAX_BAND_DISAGREEMENT == 1


def test_a_detection_two_bands_away_invalidates_the_anchor() -> None:
    """A ``far`` box (> 15 m) sitting in a column the depth head puts at
    1.2 m is the disagreement Sec 2.3 is watching for.
    """
    anchor = anchor_for_frame(_profiles(), (_detection(5, 0.5, "far"),))

    assert anchor.verdict is AnchorVerdict.BAND_DISAGREEMENT


def test_a_detection_one_band_away_is_tolerated() -> None:
    anchor = anchor_for_frame(_profiles(), (_detection(5, 0.5, "mid"),))

    assert anchor.accepted


def test_a_detection_in_a_crop_with_no_depth_cannot_contradict_anything() -> None:
    anchor = anchor_for_frame(_profiles(), (_detection(1, 0.5, "far"),))

    assert anchor.accepted


def test_a_detection_without_a_band_is_ignored() -> None:
    anchor = anchor_for_frame(_profiles(), (_detection(5, 0.5, None),))

    assert anchor.accepted


def test_a_detection_over_a_column_with_no_depth_is_ignored() -> None:
    columns = [NO_EVIDENCE_DEPTH] * COLUMNS_PER_CROP
    columns[0] = depth_for_distance_m(WALL_M)

    anchor = anchor_for_frame((make_gap_profile(5, columns),), (_detection(5, 0.9, "far"),))

    assert anchor.accepted


def test_the_anchor_is_immutable() -> None:
    anchor = anchor_for_frame(_profiles(), ())

    with pytest.raises((AttributeError, TypeError)):
        anchor.scale_m = 2.0  # type: ignore[misc]


def test_a_hand_built_anchor_needs_a_positive_scale() -> None:
    with pytest.raises(ValueError):
        DepthAnchor(scale_m=0.0, verdict=AnchorVerdict.ACCEPTED)


# --------------------------------------------------------------------------
# Step 27: the affine pair, arriving over the wire, one pair per crop
# --------------------------------------------------------------------------

#: The affine pairs measured on the three forward crops of
#: `docs/steps/assets/step-06-live-equirect.jpg` (plan Sec 2.5). They are the
#: evidence that a scale cannot be per *frame*: same frame, three inferences,
#: three unrelated pairs.
MEASURED_PAIRS: dict[int, tuple[float, float]] = {
    5: (3.658, 2.509),
    0: (0.905, 3.519),
    1: (1.373, 3.810),
}


def test_the_offset_is_the_one_term_the_conversion_was_missing() -> None:
    """`d = a / (z - b)`, and its inverse. Step 23 shipped `d = a / z`, which is
    the `b = 0` special case a one-parameter fit assumes and the measurement
    refuted.
    """
    scale, offset = 3.658, 2.509

    depth = depth_for_distance_m(WALL_M, scale, offset)

    assert depth == pytest.approx(scale / WALL_M + offset)
    assert distance_for_depth(depth, scale, offset) == pytest.approx(WALL_M)


def test_ignoring_the_offset_would_mis_place_a_row_by_the_measured_margin() -> None:
    """Why this is not a rounding detail, in the direction the measurement says.

    With the real `b = 2.509`, reading a depth as `a / z` instead of
    `a / (z - b)` puts a 1.2 m wall at 0.66 m -- a 45 % error, and one that
    saturates `proximity` at 1.0 where the truth is 0.75. Worse than the
    magnitude is that the factor is not constant: it depends on `z`, so the
    *ranking* of the 48 columns distorts too, and the direction moves with it.
    """
    scale, offset = 3.658, 2.509
    depth = depth_for_distance_m(1.2, scale, offset)

    honest = distance_for_depth(depth, scale, offset)
    forgetful = distance_for_depth(depth, scale, 0.0)

    assert honest == pytest.approx(1.2)
    assert forgetful == pytest.approx(0.658, abs=1e-3)
    # Not a constant rescaling: a 3 m row shrinks by a different factor again.
    far = depth_for_distance_m(3.0, scale, offset)
    assert distance_for_depth(far, scale, 0.0) == pytest.approx(0.981, abs=1e-3)
    # 1.2 m -> 0.658 (x0.55) but 3.0 m -> 0.981 (x0.33): the whole 48-column
    # ranking compresses, so `b = 0` costs direction as well as urgency.


def test_a_depth_at_or_below_the_models_horizon_saturates_rather_than_vanishing() -> None:
    """`z -> b` as `d -> infinity`, so `z <= b` is the sky, not an absence. It
    must read as "very far" and not as `None`: absence means "we did not look",
    and this column was looked at.
    """
    scale, offset = 1.0, 3.5

    assert distance_for_depth(offset, scale, offset) == pytest.approx(DISTANCE_MAX_M)
    assert distance_for_depth(offset - 1.0, scale, offset) == pytest.approx(DISTANCE_MAX_M)
    # A non-positive raw column is still absence: that is the cloud's own
    # "nothing here" convention and it outranks the affine reading.
    assert distance_for_depth(NO_EVIDENCE_DEPTH, scale, offset) is None


def test_the_frames_anchor_carries_one_affine_pair_per_crop() -> None:
    """Depth Anything V2's affine freedom is per *inference*, and each forward
    crop is its own inference: measured `a` 3.658 / 0.905 / 1.373 and `b` 2.509 /
    3.519 / 3.810 on one frame. One pair per frame would convert two crops with
    a third crop's units, so the anchor holds three.
    """
    profiles = tuple(
        make_gap_profile(
            crop,
            [pair[0] / WALL_M + pair[1]] * COLUMNS_PER_CROP,
            ground_scale=pair[0],
            ground_offset=pair[1],
        )
        for crop, pair in MEASURED_PAIRS.items()
    )

    anchor = anchor_for_frame(profiles, ())

    assert anchor.accepted
    for crop, (scale, offset) in MEASURED_PAIRS.items():
        depth = scale / WALL_M + offset
        assert anchor.distance_m(depth, crop) == pytest.approx(WALL_M), crop
    # And the pairs are not interchangeable: crop 0's units on crop 5's depth
    # is a different metre, which is the whole reason they travel separately.
    depth_5 = MEASURED_PAIRS[5][0] / WALL_M + MEASURED_PAIRS[5][1]
    assert anchor.distance_m(depth_5, 0) != pytest.approx(WALL_M)


def test_an_old_cloud_that_sends_no_scale_cannot_be_anchored() -> None:
    """Backward compatibility with teeth. A response from before step 27 carries
    `anchored` but no numbers, so there are no metres -- and the placeholder
    constant must not be quietly substituted, which would be the silent fake
    step 8 forbids. The frame falls back to the unanchored path.
    """
    profiles = tuple(
        make_gap_profile(
            crop, [1.0] * COLUMNS_PER_CROP, ground_scale=None, ground_offset=None
        )
        for crop in (5, 0, 1)
    )

    anchor = anchor_for_frame(profiles, ())

    assert anchor.verdict is AnchorVerdict.NO_SCALE
    assert not anchor.accepted


def test_one_crop_without_a_scale_costs_the_whole_frames_anchor() -> None:
    """Same reading as the residual guard: `d_min` spans crops, so one number
    cannot be half in metres.
    """
    scaled = make_gap_profile(5, [1.0] * COLUMNS_PER_CROP)
    bare = make_gap_profile(
        0, [1.0] * COLUMNS_PER_CROP, ground_scale=None, ground_offset=None
    )

    assert anchor_for_frame((scaled, bare), ()).verdict is AnchorVerdict.NO_SCALE


def test_a_producer_that_rejected_its_own_fit_is_refused_before_the_scale_is_read() -> None:
    """The order of the verdicts is load-bearing: a profile that carries a
    *rejected* affine pair must read as `PRODUCER_UNANCHORED`, exactly as it did
    before step 27, and not as a new verdict that some consumer treats
    differently.
    """
    profiles = (
        make_gap_profile(
            5,
            [1.0] * COLUMNS_PER_CROP,
            anchored=False,
            ground_scale=3.658,
            ground_offset=2.509,
        ),
    )

    assert anchor_for_frame(profiles, ()).verdict is AnchorVerdict.PRODUCER_UNANCHORED


def test_the_band_cross_check_reads_each_crop_in_its_own_units() -> None:
    """Sec 2.3's second guard, with the affine pair in place. The detection sits
    over a column the depth head puts at 1.2 m *in that crop's units*; reading
    it in another crop's units is how a vacuous guard becomes a wrong one.
    """
    scale, offset = MEASURED_PAIRS[5]
    profiles = (
        make_gap_profile(
            5,
            [scale / WALL_M + offset] * COLUMNS_PER_CROP,
            ground_scale=scale,
            ground_offset=offset,
        ),
    )

    assert anchor_for_frame(profiles, (_detection(5, 0.5, "near"),)).accepted
    assert (
        anchor_for_frame(profiles, (_detection(5, 0.5, "far"),)).verdict
        is AnchorVerdict.BAND_DISAGREEMENT
    )
