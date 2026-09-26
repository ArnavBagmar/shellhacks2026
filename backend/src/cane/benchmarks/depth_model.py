"""Official Depth Anything V2 Small adapter, with explicit device and timing."""
from __future__ import annotations

import hashlib
import subprocess
import sys
import time
from pathlib import Path

from .yolo import synchronize

OFFICIAL_REVISION = "a561b849ebae10a6f5ef49e26c83cbbcd36c71bf"
BACKEND_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_REPO = BACKEND_ROOT / "third_party/Depth-Anything-V2"
DEFAULT_WEIGHTS = BACKEND_ROOT / "checkpoints/depth_anything_v2_vits.pth"


def load_depth(repo: Path, weights: Path, device: str):
    if not (repo / "depth_anything_v2/dpt.py").is_file():
        raise ValueError(f"Official implementation missing at {repo}. Follow docs/depth-benchmark.md setup.")
    if not weights.is_file():
        raise ValueError(f"Small checkpoint missing: {weights}. Follow docs/depth-benchmark.md download command.")
    try:
        revision = subprocess.check_output(["git", "-C", str(repo), "rev-parse", "HEAD"], text=True).strip()
    except (OSError, subprocess.CalledProcessError):
        revision = "unknown"
    if revision != OFFICIAL_REVISION:
        raise ValueError(f"Expected official source revision {OFFICIAL_REVISION}, found {revision}. See setup instructions.")
    import cv2
    import torch
    sys.path.insert(0, str(repo.resolve()))
    from depth_anything_v2.dpt import DepthAnythingV2, Compose, Resize, NormalizeImage, PrepareForNet

    class MeasuredDepth(DepthAnythingV2):
        # infer_image itself is inherited unchanged: interpolation to original H,W
        # and CPU NumPy conversion remain in the official implementation.
        def image2tensor(self, raw_image, input_size=518):
            synchronize(torch, device)
            start = time.perf_counter()
            transform = Compose([
                Resize(width=input_size, height=input_size, resize_target=False,
                       keep_aspect_ratio=True, ensure_multiple_of=14,
                       resize_method="lower_bound", image_interpolation_method=cv2.INTER_CUBIC),
                NormalizeImage(mean=[0.485, 0.456, 0.406], std=[0.229, 0.224, 0.225]),
                PrepareForNet(),
            ])
            image = cv2.cvtColor(raw_image, cv2.COLOR_BGR2RGB) / 255.0
            image = transform({"image": image})["image"]
            # Upstream auto-selects input device even if the model was moved to CPU.
            # Preserve its transforms, but honor the user's explicit model device.
            image = torch.from_numpy(image).unsqueeze(0).to(device)
            self.input_shape = list(image.shape)
            synchronize(torch, device)
            self.preprocess_ms = (time.perf_counter() - start) * 1000
            return image, raw_image.shape[:2]

        def forward(self, image):
            synchronize(torch, device)
            start = time.perf_counter()
            result = super().forward(image)
            synchronize(torch, device)
            self.inference_ms = (time.perf_counter() - start) * 1000
            return result

    model = MeasuredDepth(encoder="vits", features=64, out_channels=[48, 96, 192, 384])
    model.load_state_dict(torch.load(weights, map_location="cpu", weights_only=True), strict=True)
    model.to(device).eval()
    with weights.open("rb") as stream:
        digest = hashlib.file_digest(stream, "sha256").hexdigest()
    return model, {"official_revision": revision, "weights_sha256": digest, "weights": str(weights.resolve())}


def predict_depth(model, frame, input_size: int, torch, device: str):
    synchronize(torch, device)
    start = time.perf_counter()
    with torch.inference_mode():
        depth = model.infer_image(frame, input_size)
    synchronize(torch, device)
    elapsed = (time.perf_counter() - start) * 1000
    if depth.shape != frame.shape[:2]:
        raise ValueError(f"Depth shape {depth.shape} does not match original image {frame.shape[:2]}")
    return depth, {
        "predict_ms": elapsed, "preprocess_ms": model.preprocess_ms,
        "inference_ms": model.inference_ms,
        "postprocess_ms": max(0.0, elapsed - model.preprocess_ms - model.inference_ms),
    }


def normalize_depth(depth):
    import numpy as np
    if depth.ndim != 2 or not depth.size or not np.isfinite(depth).all():
        raise ValueError("Depth must be a nonempty finite HxW array")
    low, high = float(depth.min()), float(depth.max())
    span = high - low
    relative = (depth - low) / span if span > 1e-6 else np.zeros_like(depth)
    return relative.astype(np.float32), low, high, span <= 1e-6
