"""Pick a detector from settings, importing the heavy one lazily (A4).

`ultralytics` (and therefore torch) is imported *inside* the branch that
needs it, so importing this module -- and the whole app -- stays torch-free
in the test environment, where torch is not installed at all.
"""

from __future__ import annotations

import logging

from cane.cloud.detector.base import FrameDetector
from cane.cloud.detector.fake import ScriptedDetector
from cane.cloud.settings import CloudSettings

logger = logging.getLogger(__name__)

TENSORRT_DEVICE = "0"


def build_detector(settings: CloudSettings) -> FrameDetector:
    """One class serves `.pt` and `.engine`: `ultralytics.YOLO` loads both
    through the identical `predict` API, so there is no second code path to
    keep in sync.
    """
    if settings.detector == "fake":
        return ScriptedDetector()

    from cane.cloud.detector.ultralytics_yolo import UltralyticsDetector

    if settings.detector == "tensorrt":
        return UltralyticsDetector(
            weights=settings.engine_path, device=TENSORRT_DEVICE, conf=settings.conf_detect,
            iou=settings.iou, max_det=settings.max_det_per_crop, half=True, label="tensorrt",
        )
    return UltralyticsDetector(
        weights=settings.weights_path, device=settings.device, conf=settings.conf_detect,
        iou=settings.iou, max_det=settings.max_det_per_crop, half=False, label="ultralytics",
    )
