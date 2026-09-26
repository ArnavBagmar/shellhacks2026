# Verified on an NVIDIA L4 (us-east1, 2026-09-26): the dynamic export failed there, the static fallback below built in 323 s and serves 640 and 512.
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
# Both are overridable for a retry without a rebuild (CANE_EXPORT_BATCH,
# CANE_EXPORT_WORKSPACE_GIB); the sidecar fingerprint carries the batch.
EXPORT_BATCH = int(os.environ.get("CANE_EXPORT_BATCH", "12"))
# Verified on an L4 (2026-09-26): at max shape (12, 3, 640, 640) the attention
# block's best tactic asks for ~8 GB of builder workspace; with the earlier
# 4 GiB cap TensorRT skipped it and failed with "could not find any
# implementation" for that node. 14 GiB fits comfortably in the L4's 23 GB.
WORKSPACE_GIB = int(os.environ.get("CANE_EXPORT_WORKSPACE_GIB", "14"))
# Fallback when the dynamic build fails (it did on the L4 with TensorRT 10.13:
# "could not find any implementation" for the attention block). A static
# batch-6 engine at 640 still serves 512 crops -- ultralytics letterboxes them
# to the engine's fixed size -- so both rung sizes keep working.
STATIC_BATCH = 6
TMP_SUFFIX = ".tmp"
SIDECAR_SUFFIX = ".json"
EXIT_FAILED = 1


def fingerprint(dynamic: bool = True) -> dict[str, Any]:
    """What the cached engine must have been built against to be reusable."""
    import tensorrt
    import torch
    import ultralytics

    return {
        "gpu_name": torch.cuda.get_device_name(0),
        "trt_version": tensorrt.__version__,
        "ultralytics_version": ultralytics.__version__,
        "imgsz": EXPORT_IMGSZ,
        "batch": EXPORT_BATCH if dynamic else STATIC_BATCH,
        "dynamic": dynamic,
    }


def static_variant(expected: dict[str, Any]) -> dict[str, Any]:
    """The sidecar the static fallback of `expected` would carry."""
    return {**expected, "batch": STATIC_BATCH, "dynamic": False}


def cache_is_valid(out: Path, expected: dict[str, Any]) -> bool:
    """True when the cached engine matches this GPU/toolchain in either the
    dynamic or the static-fallback form."""
    sidecar = out.with_suffix(out.suffix + SIDECAR_SUFFIX)
    if not out.is_file() or not sidecar.is_file():
        return False
    try:
        recorded = json.loads(sidecar.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning("unreadable engine sidecar %s: %s", sidecar, exc)
        return False
    return recorded in (expected, static_variant(expected))


def export(weights: Path, out: Path, dynamic: bool = True) -> None:
    """Build the engine, then move it into place atomically: a crashed or
    OOM-killed export must never leave a truncated .engine behind.
    """
    from ultralytics import YOLO

    out.parent.mkdir(parents=True, exist_ok=True)
    # device=0 is mandatory: on CPU the exporter disables half and then
    # asserts not dynamic.
    produced = YOLO(str(weights)).export(
        format="engine", imgsz=EXPORT_IMGSZ,
        batch=EXPORT_BATCH if dynamic else STATIC_BATCH, dynamic=dynamic,
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
        recorded = expected
        try:
            export(args.weights, args.out, dynamic=True)
        except Exception as exc:  # noqa: BLE001 - the static engine is the documented escape hatch
            logger.warning("dynamic export failed (%s: %s); building the static batch-%d fallback",
                           type(exc).__name__, exc, STATIC_BATCH)
            export(args.weights, args.out, dynamic=False)
            recorded = static_variant(expected)
        sidecar = args.out.with_suffix(args.out.suffix + SIDECAR_SUFFIX)
        sidecar.write_text(json.dumps(recorded, indent=2), encoding="utf-8")
    except Exception:
        # Exit loudly so the container dies and the VM restart policy retries.
        logger.exception("engine export failed")
        return EXIT_FAILED
    logger.info("engine ready: %s", args.out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
