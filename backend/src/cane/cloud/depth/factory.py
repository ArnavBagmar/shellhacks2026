"""Pick a depth backend from settings, importing the heavy ones lazily.

The posture rule, and why it is not simply "on":

`CANE_DEPTH_ENABLED` defaults to **true**, because step 22 could not touch the
tier-2 deploy files and there must be no new env var on the L4 to forget. Which
*backend* then runs follows the detector, which the container already sets:

| `CANE_DETECTOR` | depth backend | meaning                                  |
|---|---|---|
| `fake`          | `off`         | a dev/test process with no model in it   |
| `ultralytics`   | `torch`       | the laptop, MPS or CPU                   |
| `tensorrt`      | `tensorrt`    | the L4, with the torch path as fallback  |

`CANE_DEPTH_BACKEND` overrides all of it, including back to `off`.
"""

from __future__ import annotations

import logging
from typing import Final

from cane.cloud.depth.base import DepthEstimator, DepthLoader
from cane.cloud.depth.fake import ScriptedDepth
from cane.cloud.depth.head import DepthHead
from cane.cloud.settings import CloudSettings, DepthBackendName

logger = logging.getLogger(__name__)

OFF: Final = "off"
_POSTURE: Final[dict[str, DepthBackendName]] = {
    "fake": "off",
    "ultralytics": "torch",
    "tensorrt": "tensorrt",
}


def resolve_backend(settings: CloudSettings) -> DepthBackendName:
    """Pure, and the only place the posture table is read."""
    if not settings.depth_enabled:
        return OFF
    if settings.depth_backend is not None:
        return settings.depth_backend
    return _POSTURE[settings.detector]


def load_with_fallback(primary: DepthLoader, fallback: DepthLoader) -> DepthEstimator:
    """Run `fallback` when `primary` will not load, and say so in the log.

    `export_engine.py` does the same for the detector: the documented escape
    hatch is a slower path that works, never a dead service. If both fail the
    second exception propagates, and the head turns it into absence.
    """
    try:
        return primary()
    except Exception as exc:  # noqa: BLE001 - the fallback is the whole point
        logger.warning(
            "the primary depth backend did not load (%s: %s); falling back",
            type(exc).__name__, exc,
        )
        return fallback()


def build_depth_head(settings: CloudSettings) -> DepthHead | None:
    """`None` means this process serves no depth at all -- and says so once.

    Nothing heavy is imported here: the head loads its estimator on first use,
    so building the app stays torch-free and `/warmup` owns the cold start.
    """
    backend = resolve_backend(settings)
    if backend == OFF:
        logger.info("depth head off (detector=%s, enabled=%s)", settings.detector, settings.depth_enabled)
        return None
    return DepthHead(
        _loader(backend, settings),
        label=backend,
        max_residual=settings.depth_max_ground_residual,
    )


def _loader(backend: DepthBackendName, settings: CloudSettings) -> DepthLoader:
    if backend == "fake":
        return lambda: ScriptedDepth(input_size=settings.depth_input_size)

    def torch_loader() -> DepthEstimator:
        from cane.cloud.depth.torch_depth import load_torch_depth

        return load_torch_depth(settings)

    if backend == "torch":
        return torch_loader

    def tensorrt_loader() -> DepthEstimator:
        from cane.cloud.depth.trt_depth import load_tensorrt_depth

        return load_tensorrt_depth(settings)

    return lambda: load_with_fallback(tensorrt_loader, torch_loader)
