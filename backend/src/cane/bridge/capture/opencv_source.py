"""File and camera-index sources built on ``cv2.VideoCapture``.

Ported from ~/Desktop/insta360 (Arnav's prototype), reduced to what the
bridge needs: a sequential file source (tests, recorded walks) and an index
source for non-macOS or for the X4's 1920x1080 flat mode. The macOS 2880x1440
panorama mode is *not* reachable through OpenCV -- see
``avfoundation_source`` and ``docs/insta360feed.md``.

There is no background reader in here: keeping the newest frame is
``GrabThread``'s job, so every source stays a plain pull interface.
"""

from __future__ import annotations

import logging
from pathlib import Path

import cv2

from cane.bridge.capture.base import Frame
from cane.transport.wall_clock import now_ms

logger = logging.getLogger(__name__)

BUFFER_SIZE_FRAMES = 1  # honoured by some backends, ignored by macOS AVFoundation


class SourceOpenError(RuntimeError):
    """A video source could not be opened, or cannot deliver what was asked."""


class OpenCVSource:
    """One class for both kinds; the target decides what ``cv2`` opens."""

    def __init__(
        self,
        target: int | str,
        name: str,
        frame_size: tuple[int, int] | None = None,
    ) -> None:
        self._name = name
        self._target = target
        self._frame_size = frame_size
        self._seq = 0
        self._cap = self._open()

    @property
    def name(self) -> str:
        return self._name

    @property
    def fps(self) -> float:
        fps = self._cap.get(cv2.CAP_PROP_FPS)
        return float(fps) if fps and fps > 0 else 0.0

    def read(self) -> Frame | None:
        ok, image = self._cap.read()
        if not ok or image is None:
            return None
        self._seq += 1
        return Frame(image=image, t_arrival_laptop_ms=now_ms(), seq=self._seq)

    def close(self) -> None:
        self._cap.release()

    def _open(self) -> cv2.VideoCapture:
        cap = cv2.VideoCapture(self._target)
        if not cap.isOpened():
            raise SourceOpenError(f"could not open video source {self._target!r} ({self._name})")
        cap.set(cv2.CAP_PROP_BUFFERSIZE, BUFFER_SIZE_FRAMES)
        if self._frame_size is not None:
            cap.set(cv2.CAP_PROP_FRAME_WIDTH, self._frame_size[0])
            cap.set(cv2.CAP_PROP_FRAME_HEIGHT, self._frame_size[1])
            actual = (
                int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)),
                int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT)),
            )
            if actual != self._frame_size:
                logger.warning(
                    "%s: asked for %dx%d, the backend gave %dx%d",
                    self._name,
                    *self._frame_size,
                    *actual,
                )
        return cap


def open_file(path: str | Path) -> OpenCVSource:
    resolved = Path(path)
    if not resolved.is_file():
        raise SourceOpenError(f"video file not found: {resolved}")
    return OpenCVSource(str(resolved), f"file:{resolved.name}")


def open_index(index: int, frame_size: tuple[int, int] | None = None) -> OpenCVSource:
    """Camera by OpenCV index. Indices move when other cameras appear, so this
    is a fallback: prefer ``avfoundation_source.open_x4()`` on macOS.
    """
    return OpenCVSource(index, f"index:{index}", frame_size=frame_size)
