"""The L4 half: preprocessing, the engine cache, and the estimator wrapper.

Everything here is torch-free and TensorRT-free. The genuinely unverifiable
part -- deserialising an engine and running it on CUDA -- is isolated in
`trt_depth._EngineRunner` behind an injectable callable, so the *policy* around
it (shapes, dtypes, resize back to the frame, cache validity) is under test
even though the CUDA calls cannot be.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import numpy as np
import pytest
from numpy.typing import NDArray

from cane.cloud.depth.base import DepthEstimator
from cane.cloud.depth.export_depth_engine import (
    ONNX_OPSET,
    depth_cache_is_valid,
    engine_fingerprint,
)
from cane.cloud.depth.preprocess import (
    IMAGENET_MEAN,
    IMAGENET_STD,
    to_frame_size,
    to_model_input,
)
from cane.cloud.depth.trt_depth import TensorRTDepth

INPUT_SIZE = 280
EXPECTED = {
    "gpu_name": "NVIDIA L4",
    "trt_version": "10.13.2.6",
    "torch_version": "2.14.0",
    "input_size": INPUT_SIZE,
    "opset": ONNX_OPSET,
    "half": True,
}


def grey(value: int = 128, size: int = 640) -> NDArray[np.uint8]:
    return np.full((size, size, 3), value, dtype=np.uint8)


# --------------------------------------------------------------------------
# Preprocessing: the official transform, for square crops only
# --------------------------------------------------------------------------


def test_the_model_input_is_a_single_nchw_float32_batch() -> None:
    batch = to_model_input(grey(), INPUT_SIZE)
    assert batch.shape == (1, 3, INPUT_SIZE, INPUT_SIZE)
    assert batch.dtype == np.float32
    assert batch.flags["C_CONTIGUOUS"]


def test_the_normalisation_is_imagenet_on_rgb() -> None:
    """Channel order matters: the official transform converts BGR to RGB
    *before* normalising, so a blue crop normalised as red would quietly shift
    every depth. A flat grey pins mean/std per channel.
    """
    batch = to_model_input(grey(128), INPUT_SIZE)
    for channel in range(3):
        expected = (128.0 / 255.0 - IMAGENET_MEAN[channel]) / IMAGENET_STD[channel]
        assert batch[0, channel].mean() == pytest.approx(expected, abs=1e-5)


def test_bgr_to_rgb_is_not_skipped() -> None:
    image = np.zeros((64, 64, 3), dtype=np.uint8)
    image[:, :, 0] = 255  # pure blue in BGR
    batch = to_model_input(image, 28)
    blue_as_rgb_channel_2 = (1.0 - IMAGENET_MEAN[2]) / IMAGENET_STD[2]
    assert batch[0, 2].mean() == pytest.approx(blue_as_rgb_channel_2, abs=1e-4)


def test_a_non_square_frame_is_refused() -> None:
    """Our crops are square, which is exactly why a plain square resize is
    equivalent to the official keep-aspect lower-bound transform. A
    non-square frame would break that equivalence silently.
    """
    with pytest.raises(ValueError, match="square"):
        to_model_input(np.zeros((480, 640, 3), dtype=np.uint8), INPUT_SIZE)


def test_an_input_size_off_the_patch_grid_is_refused() -> None:
    with pytest.raises(ValueError, match="14"):
        to_model_input(grey(), 281)


def test_the_depth_comes_back_at_the_frames_own_size() -> None:
    small = np.linspace(0.0, 1.0, INPUT_SIZE * INPUT_SIZE, dtype=np.float32)
    resized = to_frame_size(small.reshape(INPUT_SIZE, INPUT_SIZE), 640, 640)
    assert resized.shape == (640, 640)
    assert resized.dtype == np.float32
    assert resized.min() >= 0.0 and resized.max() <= 1.0


def test_resizing_back_does_not_renormalise() -> None:
    """Plan Sec 1.1 again, at the last place a rescale could hide."""
    flat = np.full((INPUT_SIZE, INPUT_SIZE), 2.75, dtype=np.float32)
    assert to_frame_size(flat, 320, 320) == pytest.approx(2.75)


# --------------------------------------------------------------------------
# The estimator wrapper around an engine
# --------------------------------------------------------------------------


def fake_runner(calls: list[NDArray[np.float32]]) -> Any:
    def run(batch: NDArray[np.float32]) -> NDArray[np.float32]:
        calls.append(batch)
        return np.full((1, batch.shape[2], batch.shape[3]), 1.5, dtype=np.float32)

    return run


def test_the_estimator_preprocesses_runs_and_resizes_back() -> None:
    calls: list[NDArray[np.float32]] = []
    estimator = TensorRTDepth(fake_runner(calls), input_size=INPUT_SIZE, engine="/models/x.engine")
    depth = estimator.predict(grey(200, 512))
    assert depth.shape == (512, 512)
    assert depth == pytest.approx(1.5)
    assert calls[0].shape == (1, 3, INPUT_SIZE, INPUT_SIZE)
    assert isinstance(estimator, DepthEstimator)
    assert estimator.engine_ready is True
    assert estimator.name == "tensorrt"


def test_an_engine_output_of_the_wrong_rank_is_a_hard_error() -> None:
    """The head turns this into absence, which is the point: a mis-shaped
    engine output must never be reshaped into a plausible profile.
    """

    def run(_batch: NDArray[np.float32]) -> NDArray[np.float32]:
        return np.zeros((1, 3, 10, 10), dtype=np.float32)

    estimator = TensorRTDepth(run, input_size=INPUT_SIZE, engine="/models/x.engine")
    with pytest.raises(ValueError):
        estimator.predict(grey())


def test_a_squeezed_engine_output_is_accepted() -> None:
    """TensorRT may or may not keep the leading batch axis depending on how
    the ONNX graph was exported; both shapes mean the same map.
    """

    def run(batch: NDArray[np.float32]) -> NDArray[np.float32]:
        return np.full((batch.shape[2], batch.shape[3]), 0.25, dtype=np.float32)

    estimator = TensorRTDepth(run, input_size=INPUT_SIZE, engine="/models/x.engine")
    assert estimator.predict(grey(128, 256)).shape == (256, 256)


# --------------------------------------------------------------------------
# The engine cache (mirrors tests/cloud/test_export_engine_cache.py)
# --------------------------------------------------------------------------


def _write(tmp_path: Path, sidecar: dict[str, object]) -> Path:
    out = tmp_path / "depth.engine"
    out.write_bytes(b"engine")
    out.with_suffix(out.suffix + ".json").write_text(json.dumps(sidecar), encoding="utf-8")
    return out


def test_a_matching_sidecar_is_a_cache_hit(tmp_path: Path) -> None:
    assert depth_cache_is_valid(_write(tmp_path, EXPECTED), EXPECTED)


def test_a_different_gpu_toolchain_or_input_size_forces_a_rebuild(tmp_path: Path) -> None:
    for field, value in (
        ("gpu_name", "NVIDIA T4"),
        ("trt_version", "10.0.0"),
        ("input_size", 518),
        ("half", False),
    ):
        assert not depth_cache_is_valid(_write(tmp_path, {**EXPECTED, field: value}), EXPECTED)


def test_a_missing_engine_or_unreadable_sidecar_is_a_miss(tmp_path: Path) -> None:
    out = tmp_path / "depth.engine"
    assert not depth_cache_is_valid(out, EXPECTED)
    out.write_bytes(b"engine")
    out.with_suffix(out.suffix + ".json").write_text("{not json", encoding="utf-8")
    assert not depth_cache_is_valid(out, EXPECTED)


def test_the_fingerprint_needs_a_gpu_and_says_so() -> None:
    """Called on a Mac it must fail loudly, not invent a fingerprint that
    would later match a cached engine built for something else.
    """
    with pytest.raises(Exception):
        engine_fingerprint(INPUT_SIZE)


def test_the_opset_is_pinned() -> None:
    assert isinstance(ONNX_OPSET, int) and ONNX_OPSET >= 17
