"""One `GapProfile` per `crop_index` (plan Sec 3.1 ruling 3).

Step 21 left the rule to step 22 to enforce, and the rule exists because step
23 indexes the ring **by `crop_index`, never by list order**: a duplicate
would either overwrite a real crop's 16 columns or be silently ignored,
depending on which way the consumer's loop happened to be written. A validator
on the response is the one place that cannot be forgotten.
"""

from __future__ import annotations

import json

import pytest
from pydantic import ValidationError

from cane.cloud.api_models import (
    COLUMNS_PER_CROP,
    SCHEMA_VERSION,
    GapProfile,
    InferenceResponse,
)
from cane.cloud.depth.constants import DEPTH_CROPS

T_CAPTURE_MS = 1_790_123_456_789


def gap_profile(crop_index: int, first: float = 0.5) -> GapProfile:
    return GapProfile(
        crop_index=crop_index,
        columns=(first,) + (0.5,) * (COLUMNS_PER_CROP - 1),
        band_rows=(171, 542),
        ground_fit_residual=0.031,
        anchored=True,
    )


def response(*profiles: GapProfile) -> InferenceResponse:
    return InferenceResponse(
        schema=SCHEMA_VERSION,
        t_capture_ms=T_CAPTURE_MS,
        inference_ms=12.5,
        gap_profiles=profiles,
    )


def test_the_three_forward_crops_are_accepted() -> None:
    built = response(*(gap_profile(index) for index in DEPTH_CROPS))
    assert tuple(p.crop_index for p in built.gap_profiles) == DEPTH_CROPS


def test_no_profiles_at_all_is_accepted() -> None:
    assert response().gap_profiles == ()


def test_a_duplicate_crop_index_is_rejected() -> None:
    with pytest.raises(ValidationError, match="crop_index"):
        response(gap_profile(0), gap_profile(0, first=0.9))


def test_a_duplicate_is_rejected_on_the_wire_too() -> None:
    """The bridge parses this response; the guard must fire on parse, not only
    on construction, or a buggy cloud is caught nowhere.
    """
    profile = json.loads(gap_profile(5).model_dump_json())
    body = {
        "schema": SCHEMA_VERSION,
        "t_capture_ms": T_CAPTURE_MS,
        "inference_ms": 12.5,
        "gap_profiles": [profile, profile],
    }
    with pytest.raises(ValidationError, match="crop_index"):
        InferenceResponse.model_validate_json(json.dumps(body))


def test_a_third_duplicate_among_valid_crops_is_still_caught() -> None:
    with pytest.raises(ValidationError):
        response(gap_profile(5), gap_profile(0), gap_profile(1), gap_profile(1))


def test_the_error_names_the_offending_index() -> None:
    """Whoever reads this in a log at the venue has one shot at understanding it."""
    with pytest.raises(ValidationError) as caught:
        response(gap_profile(1), gap_profile(1))
    assert "1" in str(caught.value)


def test_order_is_not_constrained() -> None:
    """Ruling 3 again: consumers index by `crop_index`, so the list order is
    free and step 23 must not assume it is sorted or complete.
    """
    built = response(gap_profile(1), gap_profile(5))
    assert tuple(p.crop_index for p in built.gap_profiles) == (1, 5)


def test_all_six_crops_remain_legal_for_a_future_rear_ring() -> None:
    """Depth on C5/C0/C1 is step 22's policy, not the contract's (step 21's
    docstring is explicit). The validator must constrain uniqueness only.
    """
    built = response(*(gap_profile(index) for index in range(6)))
    assert len(built.gap_profiles) == 6
