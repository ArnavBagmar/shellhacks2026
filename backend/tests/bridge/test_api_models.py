"""The bridge<->cloud wire contract (F4). Step 7 implements the server side
against these exact models, so every bound here is a cross-lane promise.
"""

from __future__ import annotations

import json

import pytest
from pydantic import ValidationError

from cane.cloud.api_models import (
    CROP_COUNT,
    META_FIELD,
    Detection,
    InferenceMeta,
    InferenceRequest,
    InferenceResponse,
    crop_field_name,
)
from cane.contracts.awareness_event import ObjectClass, RangeBand

T_CAPTURE_MS = 1_790_000_000_123
YAWS = (0.0, 60.0, 120.0, 180.0, -120.0, -60.0)


def _meta_dict() -> dict[str, object]:
    return {
        "schema": 1,
        "t_capture_ms": T_CAPTURE_MS,
        "rung": 1,
        "crop_px": 640,
        "yaws_deg": list(YAWS),
        "session_id": "sess-1",
    }


def _detection_dict() -> dict[str, object]:
    return {
        "crop_index": 2,
        "cx": 0.5,
        "cy": 0.5,
        "w": 0.1,
        "h": 0.2,
        "cls": "cyclist",
        "confidence": 0.8,
        "track_id": "2:7",
        "closing_mps": 3.5,
        "range_band": "mid",
    }


def test_meta_round_trips_through_the_schema_alias() -> None:
    meta = InferenceMeta.model_validate(_meta_dict())

    assert meta.schema_version == 1
    assert meta.yaws_deg == YAWS
    dumped = json.loads(meta.model_dump_json(by_alias=True))
    assert dumped == _meta_dict()


def test_meta_is_frozen_and_forbids_extras() -> None:
    meta = InferenceMeta.model_validate(_meta_dict())
    with pytest.raises(ValidationError):
        meta.rung = 2  # type: ignore[misc]
    with pytest.raises(ValidationError, match="extra"):
        InferenceMeta.model_validate({**_meta_dict(), "gps": 1})


@pytest.mark.parametrize(
    "patch",
    [
        {"t_capture_ms": "1790000000123"},  # strict: no string->int coercion
        {"rung": 0},
        {"rung": 5},
        {"yaws_deg": [0.0, 60.0]},  # must be exactly six
        {"session_id": ""},
        {"schema": 2},
    ],
)
def test_meta_rejects_out_of_contract_values(patch: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        InferenceMeta.model_validate({**_meta_dict(), **patch})


def test_detection_parses_the_closed_enums() -> None:
    detection = Detection.model_validate(_detection_dict())

    assert detection.cls is ObjectClass.CYCLIST
    assert detection.range_band is RangeBand.MID
    assert detection.closing_mps == 3.5


def test_detection_allows_null_motion_fields() -> None:
    detection = Detection.model_validate(
        {**_detection_dict(), "closing_mps": None, "range_band": None}
    )
    assert detection.closing_mps is None
    assert detection.range_band is None


@pytest.mark.parametrize(
    "patch",
    [
        {"crop_index": 6},
        {"crop_index": -1},
        {"cx": 1.2},
        {"cy": -0.01},
        {"confidence": 1.5},
        {"cls": "unicorn"},
        {"track_id": ""},
        {"range_band": "close"},
        {"range_m": 4.0},  # the cloud never states metres: no such field
    ],
)
def test_detection_rejects_out_of_contract_values(patch: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        Detection.model_validate({**_detection_dict(), **patch})


def test_response_parses_from_raw_json_bytes() -> None:
    raw = json.dumps(
        {
            "schema": 1,
            "t_capture_ms": T_CAPTURE_MS,
            "inference_ms": 12.5,
            "detections": [_detection_dict()],
        }
    ).encode()

    response = InferenceResponse.model_validate_json(raw)

    assert response.t_capture_ms == T_CAPTURE_MS
    assert response.inference_ms == 12.5
    assert len(response.detections) == 1


def test_response_with_no_detections_is_valid() -> None:
    response = InferenceResponse.model_validate(
        {"schema": 1, "t_capture_ms": T_CAPTURE_MS, "inference_ms": 0.0, "detections": []}
    )
    assert response.detections == ()


def test_request_carries_exactly_six_crops_and_names_its_fields() -> None:
    request = InferenceRequest(
        meta=InferenceMeta.model_validate(_meta_dict()),
        crops=tuple(b"jpeg-%d" % i for i in range(CROP_COUNT)),
    )

    files = request.multipart_files()
    assert [name for name, _ in files] == [crop_field_name(i) for i in range(CROP_COUNT)]
    assert request.multipart_data()[META_FIELD] == request.meta.model_dump_json(by_alias=True)
    assert crop_field_name(0) == "crop_0"


def test_request_rejects_the_wrong_number_of_crops() -> None:
    with pytest.raises(ValidationError):
        InferenceRequest(
            meta=InferenceMeta.model_validate(_meta_dict()), crops=(b"a", b"b")
        )
