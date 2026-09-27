"""File/index capture and the guarded macOS-native source.

Nothing here needs a camera or PyObjC unless ``CANE_LIVE_X4=1`` is set: the
AVFoundation module must be *importable* on any platform, and only fail when
someone actually constructs the source without the extra installed.
"""

from __future__ import annotations

import os
from pathlib import Path

import cv2
import numpy as np
import pytest

from cane.bridge.capture import avfoundation_source as avf_module
from cane.bridge.capture.opencv_source import SourceOpenError, open_file
from cane.bridge.capture.synthetic_source import make_equirect_frame

LIVE_ENV_VAR = "CANE_LIVE_X4"
CLIP_W = 160
CLIP_H = 80
CLIP_FRAMES = 5
CLIP_FPS = 10.0
X4_FRAME_SIZE = (2880, 1440)


@pytest.fixture()
def clip_path(tmp_path: Path) -> Path:
    path = tmp_path / "clip.mp4"
    writer = cv2.VideoWriter(
        str(path), cv2.VideoWriter_fourcc(*"mp4v"), CLIP_FPS, (CLIP_W, CLIP_H)
    )
    assert writer.isOpened(), "OpenCV could not open an mp4 writer"
    for i in range(CLIP_FRAMES):
        writer.write(make_equirect_frame(CLIP_W, CLIP_H, 1_000 + i))
    writer.release()
    return path


def test_file_source_reads_every_frame_in_order_then_ends(clip_path: Path) -> None:
    source = open_file(clip_path)
    try:
        frames = []
        while (frame := source.read()) is not None:
            frames.append(frame)
    finally:
        source.close()

    assert len(frames) == CLIP_FRAMES
    assert [f.seq for f in frames] == list(range(1, CLIP_FRAMES + 1))
    assert all(f.image.shape == (CLIP_H, CLIP_W, 3) for f in frames)
    assert all(isinstance(f.t_arrival_laptop_ms, int) for f in frames)


def test_missing_file_is_a_clear_error(tmp_path: Path) -> None:
    with pytest.raises(SourceOpenError, match="not found"):
        open_file(tmp_path / "nope.mp4")


def test_avfoundation_module_imports_without_pyobjc() -> None:
    # Importable everywhere; the PyObjC import lives inside _load_backend().
    assert hasattr(avf_module, "AVFoundationSource")
    assert hasattr(avf_module, "X4_NAME_SUBSTRING")
    assert avf_module.X4_FRAME_SIZE == X4_FRAME_SIZE


def test_bgra_to_bgr_drops_alpha_and_row_padding() -> None:
    width, height, bytes_per_row = 2, 2, 12  # 3 BGRA pixels per row, only 2 used
    buffer = np.arange(bytes_per_row * height, dtype=np.uint8).tobytes()

    bgr = avf_module.bgra_bytes_to_bgr(buffer, width, height, bytes_per_row)

    assert bgr.shape == (height, width, 3)
    assert bgr.flags["C_CONTIGUOUS"]
    np.testing.assert_array_equal(bgr[0, 0], np.array([0, 1, 2], dtype=np.uint8))
    np.testing.assert_array_equal(bgr[1, 1], np.array([16, 17, 18], dtype=np.uint8))


@pytest.mark.skipif(
    os.environ.get(LIVE_ENV_VAR) != "1", reason=f"set {LIVE_ENV_VAR}=1 with the X4 on USB"
)
def test_live_x4_delivers_2880x1440_frames() -> None:
    source = avf_module.open_x4()
    try:
        frame = source.read()
        assert frame is not None
        assert (frame.image.shape[1], frame.image.shape[0]) == X4_FRAME_SIZE
    finally:
        source.close()


def test_the_insta360_virtual_camera_is_skipped_for_the_real_x4() -> None:
    # Insta360's desktop software adds "Insta360 Virtual Camera", listed
    # ahead of the X4 and without its 2880x1440 mode (2026-09-26).
    from cane.bridge.capture.avfoundation_source import pick_real_camera

    names = ["Insta360 Virtual Camera", "Insta360 X4"]
    assert pick_real_camera(names, str) == "Insta360 X4"
    # Only the virtual one attached means the X4 is unplugged: never use it.
    assert pick_real_camera(["Insta360 Virtual Camera"], str) is None
