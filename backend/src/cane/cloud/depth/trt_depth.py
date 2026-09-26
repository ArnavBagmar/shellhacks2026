# UNVERIFIED as of 2026-09-26: not executed. No CUDA and no TensorRT on this Mac, and the PyPI tensorrt sdist must never be built here (step-07-design A4).
"""The TensorRT backend for the L4, with the torch path as its fallback.

Same shape as the detector's engine story (`cloud/export_engine.py`): the engine
is built on the GPU that will serve it, cached on the persistent disk, and if
anything about it is wrong the service runs the slower path instead of dying
(`depth/factory.py::load_with_fallback`).

The split here is deliberate. `TensorRTDepth` is the *policy* -- preprocess,
run, check the shape, resize back -- and is fully under test with a fake runner.
`_EngineRunner` is the ~40 lines of CUDA and TensorRT calls that cannot be
executed, let alone measured, from a Mac; plan Sec 4.5's 25 ms target is on the
L4 and step 25 is what measures it. No number in this file is a measurement.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Callable

import numpy as np
from numpy.typing import NDArray

from cane.cloud.depth.constants import DEFAULT_INPUT_SIZE
from cane.cloud.depth.preprocess import to_frame_size, to_model_input
from cane.cloud.settings import CloudSettings

logger = logging.getLogger(__name__)

#: `(1, 3, S, S)` float32 in, one depth map out -- with or without a leading
#: batch axis, depending on how the ONNX graph was exported.
EngineRun = Callable[[NDArray[np.float32]], NDArray[np.float32]]


class TensorRTDepth:
    """`DepthEstimator` over a callable that runs one engine inference."""

    def __init__(self, run: EngineRun, input_size: int, engine: str) -> None:
        self.name = "tensorrt"
        self.engine_ready = True
        self.input_size = input_size
        self.engine = engine
        self._run = run

    def predict(self, image: NDArray[np.uint8]) -> NDArray[np.float32]:
        batch = to_model_input(image, self.input_size)
        raw = np.squeeze(np.asarray(self._run(batch), dtype=np.float32))
        if raw.ndim != 2:
            # Refusing beats reshaping: the head turns this into absence, and a
            # mis-shaped engine output must never become a plausible profile.
            raise ValueError(f"the engine returned {raw.shape}, which is not one depth map")
        return to_frame_size(raw, int(image.shape[0]), int(image.shape[1]))


class _EngineRunner:
    """Deserialise an engine once, then run it per frame. UNVERIFIED.

    Device buffers are torch tensors so the engine needs no separate CUDA
    allocator: the NGC image the container is built from already provides both
    torch and TensorRT (step-07-design A11).
    """

    def __init__(self, engine_path: Path, input_size: int, device_index: int = 0) -> None:
        import tensorrt as trt
        import torch

        if not engine_path.is_file():
            raise FileNotFoundError(f"no depth engine at {engine_path}")
        self._torch = torch
        self._device = torch.device(f"cuda:{device_index}")
        runtime = trt.Runtime(trt.Logger(trt.Logger.WARNING))
        with engine_path.open("rb") as stream:
            engine = runtime.deserialize_cuda_engine(stream.read())
        if engine is None:
            raise RuntimeError(f"{engine_path} did not deserialise on this GPU")
        self._engine: Any = engine
        self._context: Any = engine.create_execution_context()
        self._input_name = engine.get_tensor_name(0)
        self._output_name = engine.get_tensor_name(1)
        self._input_size = input_size
        logger.info("depth engine ready: %s (%s -> %s)", engine_path, self._input_name, self._output_name)

    def __call__(self, batch: NDArray[np.float32]) -> NDArray[np.float32]:
        torch = self._torch
        device_in = torch.from_numpy(batch).to(self._device, dtype=torch.float16)
        self._context.set_input_shape(self._input_name, tuple(device_in.shape))
        shape = tuple(self._context.get_tensor_shape(self._output_name))
        device_out = torch.empty(shape, dtype=torch.float16, device=self._device)
        self._context.set_tensor_address(self._input_name, int(device_in.data_ptr()))
        self._context.set_tensor_address(self._output_name, int(device_out.data_ptr()))
        stream = torch.cuda.current_stream(self._device)
        if not self._context.execute_async_v3(stream.cuda_stream):
            raise RuntimeError("the depth engine refused to enqueue")
        stream.synchronize()
        return device_out.float().cpu().numpy()


def load_tensorrt_depth(settings: CloudSettings) -> TensorRTDepth:
    """Build the engine-backed estimator, or raise so the fallback can run."""
    engine_path = settings.depth_engine_path
    runner = _EngineRunner(engine_path, settings.depth_input_size)
    return TensorRTDepth(
        runner,
        input_size=settings.depth_input_size or DEFAULT_INPUT_SIZE,
        engine=str(engine_path),
    )
