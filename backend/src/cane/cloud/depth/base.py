"""The depth-estimator boundary. Zero heavy imports, like `detector/base.py`.

This module must stay importable where torch does not exist at all: the cloud
test suite runs in an environment without it, and the local-fallback path
imports the service in-process on the laptop.
"""

from __future__ import annotations

from typing import Callable, Protocol, runtime_checkable

import numpy as np
from numpy.typing import NDArray


@runtime_checkable
class DepthEstimator(Protocol):
    """Relative depth for one crop.

    `predict` takes the crop exactly as `cv2.imdecode` produced it -- BGR, HWC,
    uint8 -- and returns an `HxW` float32 map of **raw** relative inverse
    depth at the frame's own size. Larger means nearer. It must not normalise:
    that is the caller's whole reason for existing (plan Sec 1.1).
    """

    name: str
    engine_ready: bool
    input_size: int

    def predict(self, image: NDArray[np.uint8]) -> NDArray[np.float32]: ...


#: Called at most once per process. Raising is a supported outcome: the head
#: turns it into absence, not into an exception the request has to survive.
DepthLoader = Callable[[], DepthEstimator]
