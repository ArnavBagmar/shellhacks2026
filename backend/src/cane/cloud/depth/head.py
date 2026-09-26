"""The depth head: three crops in, up to three `GapProfile`s out.

This is where plan Sec 4.2's no-silent-fake rule is enforced, and it is the
only place in step 22 that is allowed to swallow an exception. Everything it
swallows it logs, and what it returns instead is *absence*:

* the model will not load  -> no profiles, ever again this process (one attempt);
* one crop raises          -> that crop is absent, the other two still ship;
* the map is the wrong shape or full of NaN -> that crop is absent.

A zero-filled `GapProfile` would validate perfectly and read downstream as a
wall at the wearer's feet in every direction, so it is never built.

Timeless: `perf_counter` measures a duration and nothing here reads a clock.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Final, Sequence

import numpy as np
from numpy.typing import NDArray

from cane.cloud.api_models import GapProfile
from cane.cloud.depth.base import DepthEstimator, DepthLoader
from cane.cloud.depth.constants import (
    BGR_CHANNELS,
    DEFAULT_MAX_GROUND_RESIDUAL,
    DEPTH_CROPS,
    MS_PER_S,
)
from cane.cloud.depth.profile import build_profile

logger = logging.getLogger(__name__)

_FAILED_LOAD: Final = "the depth model failed to load; this response carries no gap profiles"


@dataclass(frozen=True, slots=True)
class DepthResult:
    """`ready` distinguishes "no model" from "the model had nothing to say"."""

    profiles: tuple[GapProfile, ...]
    depth_ms: float
    ready: bool


class DepthHead:
    """Owns the estimator's lifetime and its failure policy.

    The estimator is built by `load` on first use -- not at construction -- so
    importing the app costs nothing and `/warmup` decides when the first five
    to ten seconds of graph compilation happen.
    """

    def __init__(
        self,
        load: DepthLoader,
        *,
        label: str,
        crops: Sequence[int] = DEPTH_CROPS,
        max_residual: float = DEFAULT_MAX_GROUND_RESIDUAL,
    ) -> None:
        self._load = load
        self._label = label
        self._crops = tuple(crops)
        self._max_residual = max_residual
        self._estimator: DepthEstimator | None = None
        self._failed = False

    @property
    def label(self) -> str:
        return self._label

    @property
    def crops(self) -> tuple[int, ...]:
        return self._crops

    @property
    def max_residual(self) -> float:
        return self._max_residual

    @property
    def ready(self) -> bool:
        return self._estimator is not None

    def warm(self, crop_px: int) -> float | None:
        """Load and run once on zeros. `None` means "no depth on this box"."""
        estimator = self._estimator_or_none()
        if estimator is None:
            return None
        blank: NDArray[np.uint8] = np.zeros((crop_px, crop_px, BGR_CHANNELS), dtype=np.uint8)
        started = time.perf_counter()
        try:
            estimator.predict(blank)
        except Exception:  # noqa: BLE001 - a cold model that throws is not an outage
            logger.exception("the depth warm-up failed at crop_px=%d", crop_px)
            return None
        return (time.perf_counter() - started) * MS_PER_S

    def profiles(self, images: Sequence[NDArray[np.uint8]]) -> DepthResult:
        """One pass over `crops`, in order. Never raises."""
        started = time.perf_counter()
        estimator = self._estimator_or_none()
        if estimator is None:
            return DepthResult((), (time.perf_counter() - started) * MS_PER_S, False)
        built = [
            profile
            for index in self._crops
            if index < len(images)
            and (profile := self._one(estimator, index, images[index])) is not None
        ]
        return DepthResult(tuple(built), (time.perf_counter() - started) * MS_PER_S, True)

    def _estimator_or_none(self) -> DepthEstimator | None:
        """One load attempt per process. At 3 fps a retrying loader would
        re-import torch and re-read a missing checkpoint three times a second
        for the whole demo.
        """
        if self._estimator is not None or self._failed:
            return self._estimator
        try:
            self._estimator = self._load()
        except Exception:  # noqa: BLE001 - absence is the documented outcome
            self._failed = True
            logger.exception("%s (backend %s)", _FAILED_LOAD, self._label)
        return self._estimator

    def _one(
        self, estimator: DepthEstimator, crop_index: int, image: NDArray[np.uint8]
    ) -> GapProfile | None:
        try:
            depth = estimator.predict(image)
            if depth.shape != image.shape[:2]:
                logger.warning(
                    "crop %d: depth %s does not match the frame %s; emitting no profile",
                    crop_index, depth.shape, image.shape[:2],
                )
                return None
            profile = build_profile(crop_index, depth, max_residual=self._max_residual)
        except Exception:  # noqa: BLE001 - one bad crop must not cost the frame
            logger.exception("crop %d: depth failed; emitting no profile", crop_index)
            return None
        return None if profile is None else profile.to_gap_profile()
