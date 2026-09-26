"""A deterministic equirect source: no camera, no PyObjC, no network.

Used by the test suite (grab thread, pipeline) and by ``cane.bridge.spike4``
as the "burned-in millisecond counter" display described in
``docs/camera-sync-design.md`` Sec 4.3.
"""

from __future__ import annotations

import time

import cv2
import numpy as np

from cane.bridge.capture.base import Frame
from cane.transport.wall_clock import now_ms

DEFAULT_WIDTH = 2880
DEFAULT_HEIGHT = 1440
DEFAULT_FPS = 30.0
_COUNTER_ORIGIN_FRACTION = (0.05, 0.55)
_COUNTER_SCALE_PER_PX = 1.0 / 240.0
_COUNTER_COLOUR = (255, 255, 255)
_COUNTER_THICKNESS = 2
_GRADIENT_MAX = 255


def make_equirect_frame(width: int, height: int, counter_ms: int) -> np.ndarray:
    """A BGR equirect test image: a horizontal gradient (so dewarp output is
    visibly position-dependent) with ``counter_ms`` burned in as digits.
    """
    gradient = np.linspace(0, _GRADIENT_MAX, width, dtype=np.uint8)
    image = np.repeat(gradient[None, :, None], height, axis=0)
    image = np.repeat(image, 3, axis=2).copy()
    origin = (int(width * _COUNTER_ORIGIN_FRACTION[0]), int(height * _COUNTER_ORIGIN_FRACTION[1]))
    # Last six digits only: the full 13-digit epoch-ms counter runs off the
    # right edge of a small frame under OpenCV 4.x, clipping exactly the
    # digits that change from frame to frame.
    cv2.putText(
        image,
        f"{counter_ms % 1_000_000:06d}",
        origin,
        cv2.FONT_HERSHEY_SIMPLEX,
        max(width * _COUNTER_SCALE_PER_PX, 0.3),
        _COUNTER_COLOUR,
        _COUNTER_THICKNESS,
        cv2.LINE_AA,
    )
    return image


class SyntheticEquirectSource:
    """Paces itself at ``fps`` and stamps each frame with the laptop clock."""

    def __init__(
        self,
        width: int = DEFAULT_WIDTH,
        height: int = DEFAULT_HEIGHT,
        fps: float = DEFAULT_FPS,
        max_frames: int | None = None,
    ) -> None:
        if width <= 0 or height <= 0:
            raise ValueError(f"synthetic source needs a positive frame size, got {width}x{height}")
        if fps <= 0:
            raise ValueError(f"synthetic source needs a positive fps, got {fps}")
        self._width = width
        self._height = height
        self._period_s = 1.0 / fps
        self._max_frames = max_frames
        self._emitted = 0
        self._seq = 0
        self._next_due = time.monotonic()
        self._closed = False

    @property
    def name(self) -> str:
        return f"synthetic:{self._width}x{self._height}"

    @property
    def frame_size(self) -> tuple[int, int]:
        return (self._width, self._height)

    def read(self) -> Frame | None:
        if self._closed or (self._max_frames is not None and self._emitted >= self._max_frames):
            return None
        delay = self._next_due - time.monotonic()
        if delay > 0:
            time.sleep(delay)
        self._next_due = max(self._next_due + self._period_s, time.monotonic())
        self._emitted += 1
        self._seq += 1
        # Stamp *after* the frame exists: a real capture callback times the
        # moment the buffer arrives, not the moment the sensor was asked.
        image = make_equirect_frame(self._width, self._height, now_ms())
        return Frame(image=image, t_arrival_laptop_ms=now_ms(), seq=self._seq)

    def close(self) -> None:
        self._closed = True
