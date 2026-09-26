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
    CROP_COUNT,
    Detection,
    InferenceMeta,
    InferenceResponse,
)
from cane.contracts.awareness_event import T_CAPTURE_MS_MAX, T_CAPTURE_MS_MIN

T_CAPTURE_MS = 1_790_123_456_789
YAWS = [0.0, 60.0, 120.0, 180.0, -120.0, -60.0]
FORBIDDEN_DETECTION_FIELDS = frozenset({"range_m", "bearing_deg", "urgency", "ttl_ms"})


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
