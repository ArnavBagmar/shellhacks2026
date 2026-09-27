"""Where the scene service gets its equirect frame: the bridge's live grab
thread, or one still image for testing without the X4.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Protocol

import cv2
import numpy as np

from cane.bridge.capture.base import Frame

#: How long to wait for the next frame once the settle time is over. The X4
#: delivers ~30 fps, so one frame period plus generous slack.
NEXT_FRAME_TIMEOUT_S = 0.2
#: How long to wait for any frame at all before giving up on the X4 view.
FIRST_FRAME_TIMEOUT_S = 1.0


class EquirectProvider(Protocol):
    async def fresh_equirect(self) -> np.ndarray | None:
        """A BGR 2:1 equirect captured *now*, or ``None`` if there is none."""
        ...


class LatestFrameSource(Protocol):
    """``GrabThread`` and ``LatestFrameStore`` both have this."""

    def wait_for_new(self, last_seq: int, timeout_s: float) -> Frame | None: ...


class GrabProvider:
    """Reads the bridge's single-slot store. The frame returned is the first
    one that arrives *after* the call, so it shows the wearer standing still
    rather than the moment they were still turning to speak.
    """

    def __init__(self, source: LatestFrameSource) -> None:
        self._source = source

    async def fresh_equirect(self) -> np.ndarray | None:
        latest = await asyncio.to_thread(self._source.wait_for_new, 0, FIRST_FRAME_TIMEOUT_S)
        if latest is None:
            return None
        newer = await asyncio.to_thread(
            self._source.wait_for_new, latest.seq, NEXT_FRAME_TIMEOUT_S
        )
        return (newer or latest).image


class StillProvider:
    """One fixed equirect, e.g. ``docs/steps/assets/step-06-live-equirect.jpg``."""

    def __init__(self, image: np.ndarray) -> None:
        self._image = image

    @classmethod
    def from_path(cls, path: Path) -> StillProvider:
        image = cv2.imread(str(path), cv2.IMREAD_COLOR)
        if image is None:
            raise FileNotFoundError(f"could not read an image from {path}")
        return cls(image)

    @property
    def image(self) -> np.ndarray:
        return self._image

    async def fresh_equirect(self) -> np.ndarray | None:
        return self._image
