"""The live-snapshot helper: a labelled six-crop contact sheet under a size cap."""

from __future__ import annotations

from pathlib import Path

import cv2
import numpy as np
import pytest

from cane.bridge.dewarp import dewarp_equirect
from cane.bridge.snapshot import (
    CONTACT_SHEET_COLS,
    CONTACT_SHEET_ROWS,
    CROPS_FILENAME,
    EQUIRECT_FILENAME,
    EQUIRECT_PREVIEW_SIZE,
    MAX_SHEET_BYTES,
    build_contact_sheet,
    encode_under_cap,
    write_snapshot,
)
from cane.bridge.capture.synthetic_source import make_equirect_frame

EQUIRECT_W = 1440
EQUIRECT_H = 720
CROP_PX = 256
T_MS = 1_790_000_000_000


@pytest.fixture()
def cropset():
    image = make_equirect_frame(EQUIRECT_W, EQUIRECT_H, T_MS)
    return dewarp_equirect(image, CROP_PX, T_MS)


def test_contact_sheet_is_three_by_two_of_the_crops(cropset) -> None:
    sheet = build_contact_sheet(cropset)

    assert sheet.shape == (CROP_PX * CONTACT_SHEET_ROWS, CROP_PX * CONTACT_SHEET_COLS, 3)
    assert (CONTACT_SHEET_COLS, CONTACT_SHEET_ROWS) == (3, 2)


def test_the_sheet_burns_in_labels_so_the_geometry_is_auditable(cropset) -> None:
    plain = np.concatenate(
        [
            np.concatenate(cropset.images[0:3], axis=1),
            np.concatenate(cropset.images[3:6], axis=1),
        ],
        axis=0,
    )
    sheet = build_contact_sheet(cropset)

    assert sheet.shape == plain.shape
    assert not np.array_equal(sheet, plain)  # something was drawn on it


def test_encode_under_cap_respects_the_budget() -> None:
    noise = np.random.default_rng(0).integers(0, 255, (1280, 1920, 3), dtype=np.uint8)

    data = encode_under_cap(noise, quality=60, max_bytes=MAX_SHEET_BYTES)

    assert 0 < len(data) <= MAX_SHEET_BYTES
    assert data.startswith(b"\xff\xd8")


def test_write_snapshot_produces_both_files(tmp_path: Path) -> None:
    image = make_equirect_frame(EQUIRECT_W, EQUIRECT_H, T_MS)

    paths = write_snapshot(image, tmp_path, crop_px=CROP_PX)

    sheet_path = tmp_path / CROPS_FILENAME
    equirect_path = tmp_path / EQUIRECT_FILENAME
    assert set(paths) == {sheet_path, equirect_path}
    assert sheet_path.stat().st_size <= MAX_SHEET_BYTES
    preview = cv2.imread(str(equirect_path))
    assert (preview.shape[1], preview.shape[0]) == EQUIRECT_PREVIEW_SIZE
