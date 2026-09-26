"""Shared fixtures for the bridge tests (step 8 onwards).

JPEGs are encoded once per session: six 640x640 `cv2.imencode` calls are
milliseconds each, and the fallback tests post the same crops dozens of
times. Textured noise, not a flat colour -- a flat crop compresses to a few
hundred bytes and would make the decode cost unrepresentative.
"""

from __future__ import annotations

from typing import Callable

import cv2
import numpy as np
import pytest

from cane.cloud.api_models import CROP_COUNT, SCHEMA_VERSION, InferenceMeta

T_CAPTURE_MS = 1_790_123_456_789
YAWS = (0.0, 60.0, 120.0, 180.0, -120.0, -60.0)
JPEG_QUALITY = 70
DEFAULT_CROP_PX = 640
SESSION_ID = "bridge-step8"

Jpegs = tuple[bytes, ...]


def encode_crop(crop_px: int, seed: int, quality: int = JPEG_QUALITY) -> bytes:
    rng = np.random.default_rng(seed)
    image = rng.integers(0, 255, size=(crop_px, crop_px, 3), dtype=np.uint8)
    ok, buffer = cv2.imencode(".jpg", image, [int(cv2.IMWRITE_JPEG_QUALITY), quality])
    assert ok
    return bytes(buffer)


@pytest.fixture(scope="session")
def six_jpegs() -> Callable[..., Jpegs]:
    cache: dict[tuple[int, int], Jpegs] = {}

    def build(crop_px: int = DEFAULT_CROP_PX, quality: int = JPEG_QUALITY) -> Jpegs:
        key = (crop_px, quality)
        if key not in cache:
            cache[key] = tuple(
                encode_crop(crop_px, seed, quality) for seed in range(CROP_COUNT)
            )
        return cache[key]

    return build


def make_meta(**patch: object) -> InferenceMeta:
    base: dict[str, object] = {
        "schema": SCHEMA_VERSION,
        "t_capture_ms": T_CAPTURE_MS,
        "rung": 1,
        "crop_px": DEFAULT_CROP_PX,
        "yaws_deg": YAWS,
        "session_id": SESSION_ID,
    }
    return InferenceMeta(**{**base, **patch})  # type: ignore[arg-type]


@pytest.fixture()
def meta() -> Callable[..., InferenceMeta]:
    return make_meta
