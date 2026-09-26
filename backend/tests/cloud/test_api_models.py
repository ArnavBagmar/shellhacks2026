"""The bridge<->cloud wire contract, checked from the cloud's side (A2).

`api_models.py` was authored by step 6; these are the server-side assertions
the architect made binding on both sides. They are a *verification* step -- if
one of them ever goes red, step 6's bridge tests go red with it.
"""

from __future__ import annotations

import json

import pytest
from pydantic import ValidationError

from cane.cloud.api_models import (
    COLUMNS_PER_CROP,
    CROP_COUNT,
    Detection,
    GapProfile,
    InferenceMeta,
    InferenceResponse,
)
from cane.contracts.awareness_event import T_CAPTURE_MS_MAX, T_CAPTURE_MS_MIN

T_CAPTURE_MS = 1_790_123_456_789
YAWS = [0.0, 60.0, 120.0, 180.0, -120.0, -60.0]
FORBIDDEN_DETECTION_FIELDS = frozenset({"range_m", "bearing_deg", "urgency", "ttl_ms"})
FORBIDDEN_GAP_PROFILE_FIELDS = frozenset(
    {"t_capture_ms", "t_cloud_ms", "timestamp", "bearing_deg", "steer", "proximity"}
)


def meta_json(**patch: object) -> str:
    """The wire form. Model-level `strict=True` rejects a python list where a
    tuple is declared, so the models are exercised the way the app parses
    them -- `model_validate_json` -- not via python dicts (A2.4).
    """
    base: dict[str, object] = {
        "schema": 1,
        "t_capture_ms": T_CAPTURE_MS,
        "rung": 1,
        "crop_px": 640,
        "yaws_deg": YAWS,
        "session_id": "cloud-test",
    }
    return json.dumps({**base, **patch})


def test_schema_alias_round_trips_as_schema() -> None:
    """A field literally named `schema` shadows a BaseModel attribute, so the
    python name is `schema_version` and the wire key stays `schema` (A2.1).
    """
    meta = InferenceMeta.model_validate_json(meta_json())
    assert meta.schema_version == 1
    assert json.loads(meta.model_dump_json(by_alias=True))["schema"] == 1
    assert "schema_version" not in meta.model_dump_json(by_alias=True)


def test_t_capture_ms_outside_contract_window_is_rejected() -> None:
    """A bad stamp is a 422 at the cloud boundary, not an unbuildable event
    three hops later at the bridge (A2.2).
    """
    for bad in (T_CAPTURE_MS_MIN - 1, T_CAPTURE_MS_MAX + 1, 0, -1):
        with pytest.raises(ValidationError):
            InferenceMeta.model_validate_json(meta_json(t_capture_ms=bad))
    with pytest.raises(ValidationError):
        InferenceMeta.model_validate_json(meta_json(t_capture_ms=float(T_CAPTURE_MS)))
    with pytest.raises(ValidationError):
        InferenceMeta.model_validate_json(meta_json(t_capture_ms=str(T_CAPTURE_MS)))


def test_yaws_deg_accepts_integers() -> None:
    """A bridge serialising [0, 60, 120, ...] must not be 422'd (A2.3)."""
    meta = InferenceMeta.model_validate_json(meta_json(yaws_deg=[0, 60, 120, 180, -120, -60]))
    assert meta.yaws_deg == (0.0, 60.0, 120.0, 180.0, -120.0, -60.0)
    assert isinstance(meta.yaws_deg, tuple)


def test_yaws_deg_requires_exactly_six() -> None:
    for bad in ([0.0] * (CROP_COUNT - 1), [0.0] * (CROP_COUNT + 1), []):
        with pytest.raises(ValidationError):
            InferenceMeta.model_validate_json(meta_json(yaws_deg=bad))


def test_models_are_frozen_and_forbid_extra() -> None:
    meta = InferenceMeta.model_validate_json(meta_json())
    with pytest.raises(ValidationError):
        meta.rung = 2  # type: ignore[misc]
    with pytest.raises(ValidationError):
        InferenceMeta.model_validate_json(meta_json(range_m=3.0))
    with pytest.raises(ValidationError):
        InferenceResponse.model_validate_json(
            json.dumps({"schema": 1, "t_capture_ms": T_CAPTURE_MS, "inference_ms": 1.0,
                        "detections": [], "gpu": "L4"})
        )


def test_crop_px_must_be_multiple_of_32() -> None:
    assert InferenceMeta.model_validate_json(meta_json(crop_px=512)).crop_px == 512
    for bad in (639, 255, 1281, 0):
        with pytest.raises(ValidationError):
            InferenceMeta.model_validate_json(meta_json(crop_px=bad))


def test_session_id_pattern_rejects_path_traversal() -> None:
    """The session id keys a server-side registry; it must never be a path."""
    for bad in ("../../etc/passwd", "a/b", "a b", "a\nb", "", "x" * 65, "sess\x00"):
        with pytest.raises(ValidationError):
            InferenceMeta.model_validate_json(meta_json(session_id=bad))
    assert InferenceMeta.model_validate_json(meta_json(session_id="x4-2026.09.26:01")).session_id


def test_detection_has_no_forbidden_fields() -> None:
    assert FORBIDDEN_DETECTION_FIELDS.isdisjoint(Detection.model_fields)


# --------------------------------------------------------------------------
# GapProfile (step 21): the depth head's shape on the cloud -> bridge wire.
# Nothing here computes depth -- that is step 22.
# --------------------------------------------------------------------------


def response_json(**patch: object) -> str:
    base: dict[str, object] = {
        "schema": 1,
        "t_capture_ms": T_CAPTURE_MS,
        "inference_ms": 12.0,
        "detections": [],
    }
    return json.dumps({**base, **patch})


def gap_profile_json(**patch: object) -> str:
    base: dict[str, object] = {
        "crop_index": 0,
        "columns": [0.1 * i for i in range(COLUMNS_PER_CROP)],
        "band_rows": [96, 412],
        "ground_fit_residual": 0.031,
        "anchored": True,
    }
    return json.dumps({**base, **patch})


def test_an_old_response_without_gap_profiles_still_parses() -> None:
    """The amendment is additive: a cloud that has not been redeployed yet
    answers without the key, and the bridge must not 422 its own service
    (plan Sec 4.1, "additive only").
    """
    response = InferenceResponse.model_validate_json(response_json())
    assert response.gap_profiles == ()
    assert isinstance(response.gap_profiles, tuple)


def test_a_response_carries_gap_profiles_for_the_forward_crops() -> None:
    profiles = [
        json.loads(gap_profile_json(crop_index=index)) for index in (5, 0, 1)
    ]
    response = InferenceResponse.model_validate_json(response_json(gap_profiles=profiles))

    assert tuple(p.crop_index for p in response.gap_profiles) == (5, 0, 1)
    assert len(response.gap_profiles[0].columns) == COLUMNS_PER_CROP


def test_gap_profile_requires_exactly_sixteen_columns() -> None:
    """COLUMNS_PER_CROP is Shepherd's stage 4, verbatim; 15 or 17 columns
    would silently shift every bearing in the 48-column ring.
    """
    assert COLUMNS_PER_CROP == 16
    for count in (COLUMNS_PER_CROP - 1, COLUMNS_PER_CROP + 1, 0):
        with pytest.raises(ValidationError):
            GapProfile.model_validate_json(gap_profile_json(columns=[0.5] * count))


def test_gap_profile_columns_are_raw_relative_depth_of_any_sign() -> None:
    """Depth Anything V2 returns *relative* inverse depth on no fixed scale,
    and plan Sec 1.1 forbids per-frame normalisation because it makes an empty
    corridor look as dangerous as a wall. A 0..1 bound here would quietly
    force exactly that normalisation, so there is none.
    """
    wide = [-42.5, 0.0, 1.0, 37.25, 1_000.0] + [0.5] * (COLUMNS_PER_CROP - 5)
    profile = GapProfile.model_validate_json(gap_profile_json(columns=wide))
    assert profile.columns[0] == -42.5
    assert profile.columns[4] == 1_000.0


def test_gap_profile_rejects_a_non_finite_column() -> None:
    for bad in (float("inf"), float("-inf"), float("nan")):
        columns = [bad] + [0.5] * (COLUMNS_PER_CROP - 1)
        with pytest.raises(ValidationError):
            GapProfile.model_validate_json(gap_profile_json(columns=columns))
        with pytest.raises(ValidationError):
            GapProfile.model_validate(
                {**json.loads(gap_profile_json()), "columns": tuple(columns)}
            )


def test_gap_profile_columns_accept_integer_literals() -> None:
    profile = GapProfile.model_validate_json(gap_profile_json(columns=[1] * COLUMNS_PER_CROP))
    assert profile.columns == (1.0,) * COLUMNS_PER_CROP


def test_gap_profile_band_rows_must_be_ordered_and_non_negative() -> None:
    """(top, bottom) of the vertical obstacle-band filter. top == bottom is an
    empty band, which means the filter kept nothing -- a producer bug, not a
    frame the bridge should try to steer from.
    """
    assert GapProfile.model_validate_json(gap_profile_json(band_rows=[0, 1])).band_rows == (0, 1)
    for bad in ([412, 96], [200, 200], [-1, 412], [96, -1], [96], [96, 412, 500]):
        with pytest.raises(ValidationError):
            GapProfile.model_validate_json(gap_profile_json(band_rows=bad))


def test_gap_profile_crop_index_spans_all_six_crops() -> None:
    """Only C5/C0/C1 are populated today (plan Sec 1.2), but that is step 22's
    policy, not the contract's: the field is a crop index, so its bound is
    the crop count.
    """
    for index in range(CROP_COUNT):
        profile = GapProfile.model_validate_json(gap_profile_json(crop_index=index))
        assert profile.crop_index == index
    for bad in (-1, CROP_COUNT, 6):
        with pytest.raises(ValidationError):
            GapProfile.model_validate_json(gap_profile_json(crop_index=bad))


def test_gap_profile_residual_is_non_negative() -> None:
    assert GapProfile.model_validate_json(gap_profile_json(ground_fit_residual=0.0))
    for bad in (-0.001, float("inf"), float("nan")):
        with pytest.raises(ValidationError):
            GapProfile.model_validate_json(gap_profile_json(ground_fit_residual=bad))


def test_gap_profile_is_frozen_and_forbids_extra() -> None:
    profile = GapProfile.model_validate_json(gap_profile_json())
    with pytest.raises(ValidationError):
        profile.anchored = False  # type: ignore[misc]
    with pytest.raises(ValidationError):
        GapProfile.model_validate_json(gap_profile_json(range_m=3.0))


def test_gap_profile_requires_every_field() -> None:
    for missing in ("crop_index", "columns", "band_rows", "ground_fit_residual", "anchored"):
        payload = json.loads(gap_profile_json())
        del payload[missing]
        with pytest.raises(ValidationError):
            GapProfile.model_validate_json(json.dumps(payload))


# --------------------------------------------------------------------------
# Step 27: the recovered affine scale, `d = ground_scale / (z - ground_offset)`.
# Two numbers, because Depth Anything V2 is affine-invariant in disparity and
# one cannot carry a scale (plan Sec 2.5). Optional, because an old cloud has
# neither and must still parse.
# --------------------------------------------------------------------------


def scaled_profile_json(**patch: object) -> str:
    base = json.loads(gap_profile_json())
    base.update({"ground_scale": 3.658, "ground_offset": 2.509})
    return json.dumps({**base, **patch})


def test_a_profile_carries_the_two_numbers_metres_need() -> None:
    """`d = a / (z - b)` needs both. One number cannot express an
    affine-invariant model's scale -- measured: `b` 2.5..3.8 dominates an `a`
    of 0.9..3.7, so assuming `b = 0` mis-places a 3.49 m row at 2.92 m.
    """
    profile = GapProfile.model_validate_json(scaled_profile_json())

    assert profile.ground_scale == 3.658
    assert profile.ground_offset == 2.509
    assert profile.anchored is True


def test_an_old_response_without_a_scale_still_parses() -> None:
    """Backward compatibility, the whole reason these are optional: a cloud that
    has not been redeployed answers with neither key, and the bridge must not
    422 its own service (plan Sec 4.1, "additive only"). What it must *not* do
    is pretend: with no scale there are no metres, which is the bridge's
    `AnchorVerdict.NO_SCALE`, not a guessed number.
    """
    profile = GapProfile.model_validate_json(gap_profile_json())

    assert profile.ground_scale is None
    assert profile.ground_offset is None
    assert "ground_scale" not in json.loads(gap_profile_json())


def test_the_two_numbers_travel_together_or_not_at_all() -> None:
    """Half an affine model is not a scale, it is a trap: `a` alone is step 22's
    one-parameter fit, which the measurement rejected, and `b` alone converts
    nothing. Either both or neither.
    """
    for lonely in ({"ground_offset": None}, {"ground_scale": None}):
        with pytest.raises(ValidationError):
            GapProfile.model_validate_json(scaled_profile_json(**lonely))
    payload = json.loads(scaled_profile_json())
    del payload["ground_offset"]
    with pytest.raises(ValidationError):
        GapProfile.model_validate_json(json.dumps(payload))


def test_the_scale_must_be_positive_and_the_offset_may_be_either_sign() -> None:
    """`a <= 0` is not a scale: the model returns *inverse* depth, so `a` is the
    disparity-per-metre and a non-positive one means the fit found no floor.
    `b` is the model's own zero and is measured anywhere in -1..+4.
    """
    for bad in (0.0, -1.0, -3.658):
        with pytest.raises(ValidationError):
            GapProfile.model_validate_json(scaled_profile_json(ground_scale=bad))
    for good in (-1.25, 0.0, 3.81):
        profile = GapProfile.model_validate_json(scaled_profile_json(ground_offset=good))
        assert profile.ground_offset == good


def test_the_scale_rejects_non_finite_values() -> None:
    for bad in (float("inf"), float("-inf"), float("nan")):
        with pytest.raises(ValidationError):
            GapProfile.model_validate(
                {**json.loads(gap_profile_json()), "ground_scale": bad, "ground_offset": 1.0}
            )
        with pytest.raises(ValidationError):
            GapProfile.model_validate(
                {**json.loads(gap_profile_json()), "ground_scale": 1.0, "ground_offset": bad}
            )


def test_the_scale_accepts_integer_literals_on_the_wire() -> None:
    """Plan Sec 3.1 ruling 5, applied to every new numeric field: a JSON
    integer where a float is declared must be accepted, or a producer that
    happens to fit exactly 3.0 gets 422'd.
    """
    profile = GapProfile.model_validate_json(scaled_profile_json(ground_scale=3, ground_offset=0))

    assert profile.ground_scale == 3.0
    assert profile.ground_offset == 0.0


def test_a_scaled_profile_round_trips_byte_identically() -> None:
    profile = GapProfile.model_validate_json(scaled_profile_json())

    dumped = profile.model_dump_json()
    assert GapProfile.model_validate_json(dumped) == profile
    assert json.loads(dumped)["ground_scale"] == 3.658
    assert json.loads(dumped)["ground_offset"] == 2.509


def test_a_whole_response_carries_one_scale_per_forward_crop() -> None:
    """The scale is **per crop**, not per frame: each crop is its own inference,
    so each has its own affine pair. Measured on one real frame: `a` 3.658 /
    0.905 / 1.373 and `b` 2.509 / 3.519 / 3.810 for C5 / C0 / C1. Averaging
    them, or carrying one pair per frame, would convert two crops with a third
    crop's units.
    """
    measured = {5: (3.658, 2.509), 0: (0.905, 3.519), 1: (1.373, 3.810)}
    profiles = [
        json.loads(scaled_profile_json(crop_index=index, ground_scale=a, ground_offset=b))
        for index, (a, b) in measured.items()
    ]

    response = InferenceResponse.model_validate_json(response_json(gap_profiles=profiles))

    assert {p.crop_index: (p.ground_scale, p.ground_offset) for p in response.gap_profiles} == measured


def test_the_cloud_stays_timeless_in_the_depth_contract() -> None:
    """Sec 4.4: the depth path reads no clock, so no GapProfile field may
    carry one. ``t_capture_ms`` stays where it is -- echoed once, on the
    response.
    """
    assert FORBIDDEN_GAP_PROFILE_FIELDS.isdisjoint(GapProfile.model_fields)
