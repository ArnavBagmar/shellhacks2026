"""Dependency-free benchmark aggregation. Durations are milliseconds."""
from __future__ import annotations

import math
import statistics


def percentile(values: list[float], percent: float) -> float:
    """Linear interpolation, including small sample sets."""
    if not values:
        raise ValueError("Cannot summarize an empty run")
    ordered = sorted(values)
    position = (len(ordered) - 1) * percent / 100
    low, high = math.floor(position), math.ceil(position)
    return ordered[low] + (ordered[high] - ordered[low]) * (position - low)


def summarize(rows: list[dict]) -> dict:
    if not rows:
        raise ValueError("No frames were processed")
    timings = {}
    for name in rows[0]:
        if name.endswith("_ms"):
            values = [float(row[name]) for row in rows]
            timings[name] = {
                "mean": statistics.mean(values),
                "p50": percentile(values, 50),
                "p95": percentile(values, 95),
            }
    return {
        "frames": len(rows),
        "timings_ms": timings,
        # A run with every model layer switched off measures no work, so a rate
        # would be a division by zero rather than an infinite frame rate.
        "predict_fps": rate(timings["predict_ms"]["mean"]),
        "processed_fps": rate(timings["frame_ms"]["mean"]),
    }


def rate(mean_ms: float) -> float | None:
    return 1000 / mean_ms if mean_ms else None
