"""A deterministic depth estimator for tests and for `CANE_DEPTH_BACKEND=fake`.

Mirrors `detector/fake.py`: either replay a script of depth maps -- one per
`predict` call, the last repeating past the end -- or, with no script, return a
left-to-right linear ramp sized to the frame it was given.

The default ramp is deliberately, visibly synthetic: a *perfectly* even column
profile is something no real scene produces, so a fake that ever reaches a demo
is recognisable in the logs rather than plausible.
"""

from __future__ import annotations

from typing import Final, Sequence

import numpy as np
from numpy.typing import NDArray

from cane.cloud.depth.constants import DEFAULT_INPUT_SIZE

#: Raw relative inverse depth: larger is nearer, so the ramp is a wall on the
#: left receding to the right. The magnitudes are in the same ballpark as the
#: real model's measured 1.935..3.011 on noise.
FAKE_NEAR: Final = 3.0
FAKE_FAR: Final = 1.0


class ScriptedDepth:
    """`DepthEstimator` with no model behind it."""

    def __init__(
        self,
        maps: Sequence[NDArray[np.float32]] | None = None,
        engine_ready: bool = True,
        input_size: int = DEFAULT_INPUT_SIZE,
    ) -> None:
        self.name = "fake"
        self.engine_ready = engine_ready
        self.input_size = input_size
        self.calls = 0
        self.seen: list[NDArray[np.uint8]] = []
        self._maps = tuple(maps) if maps else ()

    def predict(self, image: NDArray[np.uint8]) -> NDArray[np.float32]:
        index = self.calls
        self.calls += 1
        self.seen.append(image)
        if self._maps:
            return self._maps[min(index, len(self._maps) - 1)]
        return ramp(int(image.shape[0]), int(image.shape[1]))


def ramp(height: int, width: int) -> NDArray[np.float32]:
    row = np.linspace(FAKE_NEAR, FAKE_FAR, width, dtype=np.float32)
    return np.ascontiguousarray(np.broadcast_to(row, (height, width)), dtype=np.float32)
