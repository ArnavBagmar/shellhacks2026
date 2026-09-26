"""The detector boundary (A4). Zero heavy imports: this module must stay
importable in an environment where torch and ultralytics do not exist.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, Sequence, runtime_checkable

import numpy as np
from numpy.typing import NDArray


@dataclass(frozen=True, slots=True)
class RawDetection:
    """One box in the crop's own pixel frame, with the detector's raw COCO
    name. Mapping to the contract's closed class list happens above this.
    """

    x1: float
    y1: float
    x2: float
    y2: float
    coco_name: str
    confidence: float

    @property
    def width_px(self) -> float:
        return self.x2 - self.x1

    @property
    def height_px(self) -> float:
        return self.y2 - self.y1


@runtime_checkable
class FrameDetector(Protocol):
    """Batched inference over the six crops of one frame, in crop order."""

    name: str
    engine_ready: bool
    gpu_name: str | None
    weights: str

    def detect(
        self, images: Sequence[NDArray[np.uint8]]
    ) -> tuple[tuple[RawDetection, ...], ...]: ...
