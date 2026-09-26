"""The torch backend: `cane.benchmarks.depth_model`, reused verbatim.

That loader pins the official Depth-Anything-V2 source revision and hashes the
checkpoint, so the model the cloud serves is provably the model the benchmark
measured (31.8 ms p50 per 640-px crop at `input_size=280` on Apple MPS,
deploy/depth_crop_timing.py, 2026-09-26). Reimplementing the preprocessing here
would quietly break that equivalence.

torch is imported inside the constructor, never at module scope: importing the
app -- and this module -- must stay torch-free (step-07-design A4).
"""

from __future__ import annotations

import logging
from pathlib import Path

import numpy as np
from numpy.typing import NDArray

from cane.cloud.depth.constants import DEFAULT_INPUT_SIZE
from cane.cloud.settings import CloudSettings

logger = logging.getLogger(__name__)


class TorchDepth:
    """Depth Anything V2 Small on one device, one crop at a time."""

    def __init__(
        self,
        repo: Path,
        weights: Path,
        device: str,
        input_size: int = DEFAULT_INPUT_SIZE,
    ) -> None:
        import torch

        from cane.benchmarks.depth_model import load_depth

        self.name = "torch"
        self.input_size = input_size
        self.weights = str(weights)
        self._device = device
        self._torch = torch
        self._model, self.provenance = load_depth(repo, weights, device)
        # "Ready to serve": there is no engine in this path, and a loaded
        # checkpoint is all the readiness it can have.
        self.engine_ready = True
        #: Diagnostics only -- the per-crop breakdown of the last predict. Never
        #: read by the request path; `/warmup` and the bench report from it.
        self.last_stats: dict[str, float] = {}
        logger.info(
            "depth ready: torch device=%s input_size=%d weights=%s revision=%s",
            device, input_size, self.weights, self.provenance.get("official_revision"),
        )

    def predict(self, image: NDArray[np.uint8]) -> NDArray[np.float32]:
        from cane.benchmarks.depth_model import predict_depth

        depth, stats = predict_depth(
            self._model, image, self.input_size, self._torch, self._device
        )
        self.last_stats = stats
        return np.asarray(depth, dtype=np.float32)


def load_torch_depth(settings: CloudSettings) -> TorchDepth:
    """`depth_device` falls back to `device`, so the laptop's `CANE_DEVICE=mps`
    warms both models without a second knob to forget.
    """
    return TorchDepth(
        repo=settings.depth_repo,
        weights=settings.depth_weights_path,
        device=settings.depth_device or settings.device,
        input_size=settings.depth_input_size,
    )
