"""Frame container and the interface every video source implements.

Ported from ~/Desktop/insta360 (Arnav's prototype), with the float
``timestamp`` replaced by an integer millisecond ``t_arrival_laptop_ms`` so
the whole bridge speaks the same unit as the transport's clock sync.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

import numpy as np


@dataclass(frozen=True, eq=False)
class Frame:
    """One captured frame. Frozen; ``eq=False`` because ndarray equality is
    elementwise and would make ``==`` ambiguous.

    ``image`` is BGR uint8 of shape ``(H, W, 3)``. ``t_arrival_laptop_ms`` is
    laptop-clock Unix-epoch milliseconds at the moment the frame reached us
    (it is *not* phone-clock time; the pipeline converts). ``seq`` increases
    by one per frame accepted into the store it came from.
    """

    image: np.ndarray
    t_arrival_laptop_ms: int
    seq: int


class FrameSource(Protocol):
    """A pull source: ``read()`` blocks until the next frame or gives up."""

    @property
    def name(self) -> str: ...

    def read(self) -> Frame | None: ...

    def close(self) -> None: ...
