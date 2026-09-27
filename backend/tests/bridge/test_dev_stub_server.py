"""The dev-only inference stub: exercises the bridge before step 7 exists.

It implements the F4 contract exactly -- same models, echoed stamp -- so a
bridge that talks to this stub talks to the real service unchanged.
"""

from __future__ import annotations

import json

import httpx
import pytest

from cane.bridge.dev_stub_server import DEFAULT_DELAY_MS, build_app, parse_args
from cane.cloud.api_models import CROP_COUNT, InferenceResponse, crop_field_name

T_CAPTURE_MS = 1_790_000_000_321
YAWS = [0.0, 60.0, 120.0, 180.0, -120.0, -60.0, 0.0]  # C6 last


def _meta(**patch: object) -> str:
    base: dict[str, object] = {
        "schema": 1,
        "t_capture_ms": T_CAPTURE_MS,
        "rung": 1,
        "crop_px": 640,
        "yaws_deg": YAWS,
        "session_id": "stub-test",
    }
    return json.dumps({**base, **patch})


def _files(count: int = CROP_COUNT) -> list[tuple[str, tuple[str, bytes, str]]]:
    return [
        (crop_field_name(i), (f"crop_{i}.jpg", b"\xff\xd8not-really-a-jpeg", "image/jpeg"))
        for i in range(count)
    ]


@pytest.fixture()
async def client() -> httpx.AsyncClient:
    app = build_app(delay_ms=0)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://stub") as c:
        yield c


async def test_infer_echoes_the_capture_stamp_byte_identically(
    client: httpx.AsyncClient,
) -> None:
    response = await client.post("/infer", data={"meta": _meta()}, files=_files())

    assert response.status_code == 200
    body = InferenceResponse.model_validate_json(response.content)
    assert body.t_capture_ms == T_CAPTURE_MS
    assert body.inference_ms >= 0.0
    assert len(body.detections) == 1
    assert "range_m" not in response.text


async def test_the_detection_sweeps_the_crops_so_bearings_sweep_the_circle(
    client: httpx.AsyncClient,
) -> None:
    indices = []
    for _ in range(CROP_COUNT + 1):
        response = await client.post("/infer", data={"meta": _meta()}, files=_files())
        indices.append(InferenceResponse.model_validate_json(response.content).detections[0].crop_index)

    assert indices[:CROP_COUNT] == list(range(CROP_COUNT))
    assert indices[CROP_COUNT] == 0  # it cycles


async def test_malformed_meta_is_rejected_with_422(client: httpx.AsyncClient) -> None:
    response = await client.post("/infer", data={"meta": "{not json"}, files=_files())
    assert response.status_code == 422


async def test_out_of_contract_meta_is_rejected_with_422(client: httpx.AsyncClient) -> None:
    response = await client.post("/infer", data={"meta": _meta(rung=9)}, files=_files())
    assert response.status_code == 422


async def test_a_missing_crop_is_rejected_with_422(client: httpx.AsyncClient) -> None:
    response = await client.post(
        "/infer", data={"meta": _meta()}, files=_files(CROP_COUNT - 1)
    )
    assert response.status_code == 422


async def test_healthz(client: httpx.AsyncClient) -> None:
    response = await client.get("/healthz")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


async def test_induced_delay_is_reported_in_inference_ms() -> None:
    app = build_app(delay_ms=120)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://stub") as client:
        response = await client.post("/infer", data={"meta": _meta()}, files=_files())

    body = InferenceResponse.model_validate_json(response.content)
    assert body.inference_ms >= 100.0


def test_cli_defaults_and_overrides() -> None:
    assert parse_args([]).delay_ms == DEFAULT_DELAY_MS
    args = parse_args(["--port", "9000", "--delay-ms", "800"])
    assert (args.port, args.delay_ms) == (9000, 800)
