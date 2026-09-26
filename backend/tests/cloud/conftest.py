"""Fixtures for the cloud service tests.

JPEGs are encoded once for the whole session: `cv2.imencode` of six 640x640
crops is milliseconds, and the overhead benchmark must measure the service,
not the test's own encoder.
"""

from __future__ import annotations

import json
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import AsyncIterator, Callable, Sequence

import cv2
import httpx
import numpy as np
import pytest

from cane.cloud.api_models import CROP_COUNT, crop_field_name
from cane.cloud.detector.base import FrameDetector
from cane.cloud.detector.fake import ScriptedDetector
from cane.cloud.settings import CloudSettings

T_CAPTURE_MS = 1_790_123_456_789
YAWS = [0.0, 60.0, 120.0, 180.0, -120.0, -60.0]
JPEG_QUALITY = 70
Files = list[tuple[str, tuple[str, bytes, str]]]


def _encode(crop_px: int, seed: int) -> bytes:
    """A textured crop: a flat colour compresses to a few hundred bytes and
    would make the decode cost unrepresentative.
    """
    rng = np.random.default_rng(seed)
    image = rng.integers(0, 255, size=(crop_px, crop_px, 3), dtype=np.uint8)
    ok, buffer = cv2.imencode(".jpg", image, [int(cv2.IMWRITE_JPEG_QUALITY), JPEG_QUALITY])
    assert ok
    return bytes(buffer)


@pytest.fixture(scope="session")
def six_jpegs() -> Callable[[int], Files]:
    cache: dict[int, Files] = {}

    def build(crop_px: int = 640) -> Files:
        if crop_px not in cache:
            cache[crop_px] = [
                (crop_field_name(i), (f"crop_{i}.jpg", _encode(crop_px, i), "image/jpeg"))
                for i in range(CROP_COUNT)
            ]
        return cache[crop_px]

    return build


@pytest.fixture()
def meta_json() -> Callable[..., str]:
    def build(**patch: object) -> str:
        base: dict[str, object] = {
            "schema": 1,
            "t_capture_ms": T_CAPTURE_MS,
            "rung": 1,
            "crop_px": 640,
            "yaws_deg": YAWS,
            "session_id": "cloud-test",
        }
        return json.dumps({**base, **patch})

    return build


@dataclass
class Harness:
    """An ASGI client plus the state the test injected, so assertions can
    reach `last_inference_ms` and the detector's call count.
    """

    client: httpx.AsyncClient
    state: object
    detector: FrameDetector


@asynccontextmanager
async def cloud_harness(
    detector: FrameDetector | None = None, **settings_overrides: object
) -> AsyncIterator[Harness]:
    """Build the real app around an injected detector.

    The state goes in through `dependency_overrides`, never a lifespan
    handler: `httpx.ASGITransport` does not run lifespan, so a lifespan-built
    detector would be `None` in every test.
    """
    from cane.cloud.app import AppState, app, build_state, get_app_state

    settings = CloudSettings(**settings_overrides)  # type: ignore[arg-type]
    chosen = detector if detector is not None else ScriptedDetector()
    state: AppState = build_state(settings, chosen)
    app.dependency_overrides[get_app_state] = lambda: state
    transport = httpx.ASGITransport(app=app)
    try:
        async with httpx.AsyncClient(transport=transport, base_url="http://cloud") as client:
            yield Harness(client=client, state=state, detector=chosen)
    finally:
        app.dependency_overrides.clear()


@pytest.fixture()
async def app_with_fake() -> AsyncIterator[Harness]:
    async with cloud_harness() as harness:
        yield harness


@pytest.fixture()
def fake_detector() -> ScriptedDetector:
    return ScriptedDetector()


def post_infer(
    harness: Harness, meta: str, files: Sequence[tuple[str, tuple[str, bytes, str]]],
    **kwargs: object,
) -> object:
    return harness.client.post("/infer", data={"meta": meta}, files=list(files), **kwargs)
