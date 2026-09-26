# UNVERIFIED as of 2026-09-26: not executed. No CUDA, no TensorRT and no GPU on this Mac; step 25 is what runs and measures this.
"""Export Depth Anything V2 Small to a TensorRT engine **on the serving GPU**.

An operator tool, not the request path -- the same tier as
`cloud/export_engine.py`, and exempt from the timeless rule for the same reason.
It follows that file's discipline exactly:

* an engine is not portable across GPU architectures, so the export runs at
  container start, not at image build;
* the result is cached on the persistent disk with a sidecar fingerprint, and a
  cache built for a different GPU, TensorRT or torch is *not* a hit;
* the engine is moved into place atomically, so an OOM-killed export cannot
  leave a truncated `.engine` behind.

Unlike YOLO's, this export has no ultralytics wrapper: it goes through ONNX at
a fixed square input size, which is why `depth/preprocess.py` exists and why
`depth_input_size` must stay on the ViT patch grid.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from pathlib import Path
from typing import Any, Sequence

logger = logging.getLogger("cane.cloud.depth.export_depth_engine")

ONNX_OPSET = 17
SIDECAR_SUFFIX = ".json"
TMP_SUFFIX = ".tmp"
EXIT_FAILED = 1
# 4 GiB: the depth encoder is a ViT-S, an order of magnitude smaller than the
# YOLO graph that needed 14 GiB for one attention tactic. Overridable without a
# rebuild, like every other export knob.
WORKSPACE_GIB = int(os.environ.get("CANE_DEPTH_EXPORT_WORKSPACE_GIB", "4"))


def engine_fingerprint(input_size: int, half: bool = True) -> dict[str, Any]:
    """What a cached engine must have been built against to be reusable.

    Raises on a machine with no GPU toolchain -- deliberately. An invented
    fingerprint would later *match* an engine built for something else.
    """
    import tensorrt
    import torch

    return {
        "gpu_name": torch.cuda.get_device_name(0),
        "trt_version": tensorrt.__version__,
        "torch_version": torch.__version__,
        "input_size": input_size,
        "opset": ONNX_OPSET,
        "half": half,
    }


def depth_cache_is_valid(out: Path, expected: dict[str, Any]) -> bool:
    """True only when the sidecar records exactly this toolchain and shape."""
    sidecar = out.with_suffix(out.suffix + SIDECAR_SUFFIX)
    if not out.is_file() or not sidecar.is_file():
        return False
    try:
        recorded = json.loads(sidecar.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning("unreadable depth engine sidecar %s: %s", sidecar, exc)
        return False
    return bool(recorded == expected)


def export_onnx(repo: Path, weights: Path, onnx_path: Path, input_size: int) -> None:
    """Trace the official model at a fixed `(1, 3, S, S)`.

    The same loader the service uses, so the exported graph is the graph that
    was benchmarked -- including the official source revision check.
    """
    import torch

    from cane.benchmarks.depth_model import load_depth

    model, provenance = load_depth(repo, weights, "cpu")
    logger.info("exporting revision %s", provenance.get("official_revision"))
    dummy = torch.zeros((1, 3, input_size, input_size), dtype=torch.float32)
    onnx_path.parent.mkdir(parents=True, exist_ok=True)
    torch.onnx.export(
        model, dummy, str(onnx_path), opset_version=ONNX_OPSET,
        input_names=["image"], output_names=["depth"], do_constant_folding=True,
    )


def build_engine(onnx_path: Path, out: Path, half: bool = True) -> None:
    """ONNX -> engine, staged through a temporary file."""
    import tensorrt as trt

    logger_trt = trt.Logger(trt.Logger.WARNING)
    builder = trt.Builder(logger_trt)
    network = builder.create_network()
    parser = trt.OnnxParser(network, logger_trt)
    if not parser.parse(onnx_path.read_bytes()):
        errors = [str(parser.get_error(i)) for i in range(parser.num_errors)]
        raise RuntimeError(f"TensorRT could not parse {onnx_path}: {errors}")
    config = builder.create_builder_config()
    config.set_memory_pool_limit(trt.MemoryPoolType.WORKSPACE, WORKSPACE_GIB << 30)
    if half:
        config.set_flag(trt.BuilderFlag.FP16)
    serialized = builder.build_serialized_network(network, config)
    if serialized is None:
        raise RuntimeError("TensorRT returned no engine for the depth network")
    out.parent.mkdir(parents=True, exist_ok=True)
    staged = out.with_suffix(out.suffix + TMP_SUFFIX)
    staged.write_bytes(bytes(serialized))
    os.replace(staged, out)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m cane.cloud.depth.export_depth_engine")
    parser.add_argument("--repo", type=Path, required=True)
    parser.add_argument("--weights", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--input-size", type=int, required=True)
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    try:
        expected = engine_fingerprint(args.input_size)
        if not args.force and depth_cache_is_valid(args.out, expected):
            logger.info("reusing cached depth engine %s for %s", args.out, expected["gpu_name"])
            return 0
        onnx_path = args.out.with_suffix(".onnx")
        export_onnx(args.repo, args.weights, onnx_path, args.input_size)
        build_engine(onnx_path, args.out)
        sidecar = args.out.with_suffix(args.out.suffix + SIDECAR_SUFFIX)
        sidecar.write_text(json.dumps(expected, indent=2), encoding="utf-8")
    except Exception:
        # Loud, but NOT fatal to the service: the torch fallback serves depth
        # while this is broken, so the container entrypoint may ignore the code.
        logger.exception("depth engine export failed; the service will fall back to torch")
        return EXIT_FAILED
    logger.info("depth engine ready: %s", args.out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
