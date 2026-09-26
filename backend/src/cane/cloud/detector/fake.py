"""A deterministic detector for tests and for the fake-path live run.

Either replay a script -- one entry per `detect` call, each entry holding one
tuple of boxes per crop -- or, with no script, emit a single person in crop 0
that grows frame by frame, which is enough to exercise tracking, banding and
closing speed end to end.
"""

from __future__ import annotations

from typing import Final, Sequence

import numpy as np
from numpy.typing import NDArray

from cane.cloud.api_models import CROP_COUNT
from cane.cloud.detector.base import RawDetection

Frame = Sequence[Sequence[RawDetection]]

DEFAULT_CONFIDENCE: Final = 0.86
DEFAULT_COCO_NAME: Final = "person"
START_HEIGHT_FRACTION: Final = 0.25
GROWTH_PER_CALL: Final = 0.02
MAX_HEIGHT_FRACTION: Final = 0.70
ASPECT: Final = 0.4


def _approaching(call: int, size: int) -> RawDetection:
    """A centred person whose box grows ~2 % of the crop per call."""
    height = size * min(START_HEIGHT_FRACTION + GROWTH_PER_CALL * call, MAX_HEIGHT_FRACTION)
    width = height * ASPECT
    centre = size / 2.0
    return RawDetection(
        x1=centre - width / 2.0,
        y1=centre - height / 2.0,
        x2=centre + width / 2.0,
        y2=centre + height / 2.0,
        coco_name=DEFAULT_COCO_NAME,
        confidence=DEFAULT_CONFIDENCE,
    )


class ScriptedDetector:
    """`FrameDetector` with no model behind it."""

    def __init__(
        self,
        frames: Sequence[Frame] | None = None,
        engine_ready: bool = True,
        gpu_name: str | None = None,
        weights: str = "scripted",
    ) -> None:
        self.name = "fake"
        self.engine_ready = engine_ready
        self.gpu_name = gpu_name
        self.weights = weights
        self._frames = tuple(frames) if frames else ()
        self._calls = 0

    @property
    def calls(self) -> int:
        return self._calls

    def detect(
        self, images: Sequence[NDArray[np.uint8]]
    ) -> tuple[tuple[RawDetection, ...], ...]:
        call = self._calls
        self._calls += 1
        if not self._frames:
            size = int(images[0].shape[0]) if len(images) else 0
            first = (_approaching(call, size),) if size else ()
            return (first,) + tuple(() for _ in range(len(images) - 1))
        # Past the end of the script the last frame repeats, so a 3-frame
        # script still answers a 50-request overhead benchmark.
        frame = self._frames[min(call, len(self._frames) - 1)]
        padded = tuple(tuple(crop) for crop in frame)
        return padded + tuple(() for _ in range(max(0, CROP_COUNT - len(padded))))
