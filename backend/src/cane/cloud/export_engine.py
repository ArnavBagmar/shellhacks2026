# UNVERIFIED as of 2026-09-26: no GPU, no gcloud, no GCP project. Nothing here has been executed.
"""Export YOLO11s to a TensorRT engine **on the GPU that will serve it**.

A TensorRT engine is not portable across GPU architectures, so the export
runs at container start rather than at image build. It is cached on a
persistent volume and skipped on later boots -- unless the sidecar says the
cache was built for a different GPU, TensorRT or ultralytics, which is
exactly what "the engine loads on a fresh L4 boot" has to survive when a
volume is re-attached to a different accelerator.

Exempt from the timeless rule: this is a startup tool, not the request path.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from pathlib import Path
from typing import Any, Sequence

logger = logging.getLogger("cane.cloud.export_engine")

EXPORT_IMGSZ = 640
# opt = batch // 2, so batch=12 puts the optimisation profile exactly on our
# real batch of six crops; min=(1,3,32,32) covers 512 as well as 640 (A11).
EXPORT_BATCH = 12
WORKSPACE_GIB = 4
TMP_SUFFIX = ".tmp"
SIDECAR_SUFFIX = ".json"
EXIT_FAILED = 1


def fingerprint() -> dict[str, Any]:
    """What the cached engine must have been built against to be reusable."""
    import tensorrt
    import torch
    import ultralytics

    return {
        "gpu_name": torch.cuda.get_device_name(0),
        "trt_version": tensorrt.__version__,
        "ultralytics_version": ultralytics.__version__,
        "imgsz": EXPORT_IMGSZ,
        "batch": EXPORT_BATCH,
    }


def cache_is_valid(out: Path, expected: dict[str, Any]) -> bool:
    sidecar = out.with_suffix(out.suffix + SIDECAR_SUFFIX)
    if not out.is_file() or not sidecar.is_file():
        return False
    try:
        return json.loads(sidecar.read_text(encoding="utf-8")) == expected
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning("unreadable engine sidecar %s: %s", sidecar, exc)
        return False


def export(weights: Path, out: Path) -> None:
    """Build the engine, then move it into place atomically: a crashed or
    OOM-killed export must never leave a truncated .engine behind.
    """
    from ultralytics import YOLO

    out.parent.mkdir(parents=True, exist_ok=True)
    # device=0 is mandatory: on CPU the exporter disables half and then
    # asserts not dynamic.
    produced = YOLO(str(weights)).export(
        format="engine", imgsz=EXPORT_IMGSZ, batch=EXPORT_BATCH, dynamic=True,
        half=True, device=0, workspace=WORKSPACE_GIB, simplify=True, nms=False,
        verbose=False,
    )
    staged = out.with_suffix(out.suffix + TMP_SUFFIX)
    os.replace(produced, staged)
    os.replace(staged, out)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m cane.cloud.export_engine")
    parser.add_argument("--weights", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    try:
        expected = fingerprint()
        if not args.force and cache_is_valid(args.out, expected):
            logger.info("reusing cached engine %s for %s", args.out, expected["gpu_name"])
            return 0
        logger.info("exporting %s -> %s for %s", args.weights, args.out, expected["gpu_name"])
        export(args.weights, args.out)
        sidecar = args.out.with_suffix(args.out.suffix + SIDECAR_SUFFIX)
        sidecar.write_text(json.dumps(expected, indent=2), encoding="utf-8")
    except Exception:
        # Exit loudly so the container dies and the VM restart policy retries.
        logger.exception("engine export failed")
        return EXIT_FAILED
    logger.info("engine ready: %s", args.out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
