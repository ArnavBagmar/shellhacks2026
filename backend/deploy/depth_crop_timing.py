"""Does the depth path step 22 will reuse actually load and run? Timed, on MPS."""
import sys, time
import numpy as np
import torch
sys.path.insert(0, "src")
from cane.benchmarks.depth_model import DEFAULT_REPO, DEFAULT_WEIGHTS, load_depth, predict_depth

DEV = "mps"
model, provenance = load_depth(DEFAULT_REPO, DEFAULT_WEIGHTS, DEV)
rng = np.random.default_rng(0)
crop = rng.integers(0, 255, (640, 640, 3), dtype=np.uint8)
for _ in range(3):
    predict_depth(model, crop, 280, torch, DEV)
times, infer = [], []
for _ in range(8):
    t = time.perf_counter()
    depth, stats = predict_depth(model, crop, 280, torch, DEV)
    times.append((time.perf_counter() - t) * 1000)
    infer.append(stats["inference_ms"])
p50 = float(np.percentile(times, 50))
print(f"per-crop total ms: p50={p50:.1f} min={min(times):.1f}  (pure inference p50={np.percentile(infer,50):.1f})")
print(f"THREE forward crops, sequential, Apple MPS: p50 ~= {3*p50:.0f} ms")
print(f"depth out: shape={depth.shape} dtype={depth.dtype} min={depth.min():.3f} max={depth.max():.3f}")
