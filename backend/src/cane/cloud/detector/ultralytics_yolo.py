"""The only module that imports ultralytics/torch (A4).

One class serves both weight formats: `ultralytics.YOLO` loads a `.pt` and a
`.engine` through the identical `predict` API, so there is no second
postprocessing path to keep in sync. The heavy imports live inside
`__init__`, so merely importing this module -- which the timeless AST test
does by reading its source, never by importing it -- costs nothing.

Classes are mapped by COCO **name**, never by index: a re-ordered `names`
table would otherwise silently relabel every hazard.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Final, Sequence

import numpy as np
from numpy.typing import NDArray

from cane.cloud.detector.base import RawDetection

logger = logging.getLogger(__name__)

ENGINE_SUFFIX: Final = ".engine"
TENSORRT_LABEL: Final = "tensorrt"


class UltralyticsDetector:
    """Batched YOLO11s over the six crops of one frame."""

    def __init__(
        self,
        weights: Path,
        device: str,
        conf: float,
        iou: float,
        max_det: int,
        half: bool,
        label: str,
    ) -> None:
        from ultralytics import YOLO

        self.name = label
        self.weights = str(weights)
        self._device = device
        self._conf = conf
        self._iou = iou
        self._max_det = max_det
        self._half = half
        self._model = YOLO(str(weights))
        # "Ready to serve", which is what step 8's health monitor acts on.
        # For the TensorRT label that additionally requires a real engine
        # file: a `.pt` served as `tensorrt` is a misconfiguration.
        self.engine_ready = label != TENSORRT_LABEL or (
            weights.suffix == ENGINE_SUFFIX and weights.exists()
        )
        self.gpu_name = _accelerator_name(device)
        logger.info(
            "detector %s ready: weights=%s device=%s half=%s engine_ready=%s gpu=%s",
            label, self.weights, device, half, self.engine_ready, self.gpu_name,
        )

    def detect(
        self, images: Sequence[NDArray[np.uint8]]
    ) -> tuple[tuple[RawDetection, ...], ...]:
        """Input must be BGR HWC uint8 -- exactly what `cv2.imdecode` returns."""
        if not images:
            return ()
        results = self._model.predict(
            list(images),
            imgsz=int(images[0].shape[0]),
            conf=self._conf,
            iou=self._iou,
            max_det=self._max_det,
            device=self._device,
            half=self._half,
            verbose=False,
        )
        return tuple(self._boxes(result) for result in results)

    def _boxes(self, result: Any) -> tuple[RawDetection, ...]:
        boxes = getattr(result, "boxes", None)
        if boxes is None or len(boxes) == 0:
            return ()
        # `xyxy` is already in the original image's pixels.
        xyxy = boxes.xyxy.cpu().numpy()
        confidence = boxes.conf.cpu().numpy()
        class_indices = boxes.cls.cpu().numpy()
        names = self._model.names
        return tuple(
            RawDetection(
                x1=float(box[0]), y1=float(box[1]), x2=float(box[2]), y2=float(box[3]),
                coco_name=str(names[int(index)]), confidence=float(score),
            )
            for box, score, index in zip(xyxy, confidence, class_indices)
        )


def _accelerator_name(device: str) -> str | None:
    """Best effort, never fatal: `/healthz` reporting `null` is not an outage."""
    try:
        import torch

        if device.isdigit() or device.startswith("cuda"):
            return str(torch.cuda.get_device_name(int(device) if device.isdigit() else 0))
        if device == "mps":
            return "apple-mps" if torch.backends.mps.is_available() else None
    except Exception as exc:  # noqa: BLE001 - diagnostics only
        logger.warning("could not read the accelerator name for %r: %s", device, exc)
    return None
