"""The debug contact sheet: useful, optional, and never in the hot path."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

import numpy as np

from cane.cloud.api_models import CROP_COUNT, Detection, InferenceResponse
from cane.cloud.debug_dump import MAX_BYTES, TILE_PX, build_sheet, write_contact_sheet

from .conftest import Harness, cloud_harness

CROP_PX = 640
T0 = 1_790_000_000_000


def _images() -> list[np.ndarray]:
    rng = np.random.default_rng(7)
    return [
        rng.integers(0, 255, size=(CROP_PX, CROP_PX, 3), dtype=np.uint8)
        for _ in range(CROP_COUNT)
    ]


def _response() -> InferenceResponse:
    detections = tuple(
        Detection(
            crop_index=index, cx=0.5, cy=0.5, w=0.2, h=0.4, cls="person",
            confidence=0.91, track_id=f"{index}:1:1", closing_mps=2.5, range_band="mid",
        )
        for index in range(CROP_COUNT)
    )
    return InferenceResponse(
        schema=1, t_capture_ms=T0, inference_ms=12.5, detections=detections
    )


def test_the_sheet_is_three_by_two_tiles() -> None:
    sheet = build_sheet(_images(), _response())
    assert sheet.shape == (TILE_PX * 2, TILE_PX * 3, 3)


def test_the_written_sheet_fits_the_size_budget(tmp_path: Path) -> None:
    path = write_contact_sheet(tmp_path, _images(), _response(), 42)
    assert path.name == "sheet-000042.jpg"
    assert 0 < path.stat().st_size <= MAX_BYTES


def test_the_sheet_does_not_mutate_the_decoded_crops() -> None:
    images = _images()
    before = [image.copy() for image in images]
    build_sheet(images, _response())
    assert all(np.array_equal(a, b) for a, b in zip(images, before))


async def test_dumping_is_off_by_default(
    app_with_fake: Harness, meta_json: Callable[..., str], six_jpegs: Callable[..., Any],
    tmp_path: Path,
) -> None:
    await app_with_fake.client.post(
        "/infer", data={"meta": meta_json()}, files=list(six_jpegs(640))
    )
    assert list(tmp_path.iterdir()) == []


async def test_enabling_the_dump_writes_one_sheet_every_n_frames(
    meta_json: Callable[..., str], six_jpegs: Callable[..., Any], tmp_path: Path
) -> None:
    async with cloud_harness(dump_dir=tmp_path, dump_every_n=2) as harness:
        for index in range(4):
            response = await harness.client.post(
                "/infer",
                data={"meta": meta_json(t_capture_ms=T0 + 333 * index)},
                files=list(six_jpegs(640)),
            )
            assert response.status_code == 200
    assert sorted(p.name for p in tmp_path.iterdir()) == [
        "sheet-000002.jpg", "sheet-000004.jpg"
    ]
