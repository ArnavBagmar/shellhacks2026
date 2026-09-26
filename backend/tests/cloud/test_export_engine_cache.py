"""The engine cache accepts the dynamic engine or its static fallback, and
nothing else. Torch-free: only the pure sidecar logic is under test."""

from __future__ import annotations

import json
from pathlib import Path

from cane.cloud.export_engine import STATIC_BATCH, cache_is_valid, static_variant

EXPECTED = {
    "gpu_name": "NVIDIA L4", "trt_version": "10.13.2.6", "ultralytics_version": "8.4.163",
    "imgsz": 640, "batch": 12, "dynamic": True,
}


def _write(tmp_path: Path, sidecar: dict[str, object]) -> Path:
    out = tmp_path / "yolo11s.engine"
    out.write_bytes(b"engine")
    out.with_suffix(out.suffix + ".json").write_text(json.dumps(sidecar), encoding="utf-8")
    return out


def test_the_dynamic_sidecar_is_a_cache_hit(tmp_path: Path) -> None:
    assert cache_is_valid(_write(tmp_path, EXPECTED), EXPECTED)


def test_the_static_fallback_sidecar_is_also_a_cache_hit(tmp_path: Path) -> None:
    fallback = static_variant(EXPECTED)
    assert fallback["batch"] == STATIC_BATCH and fallback["dynamic"] is False
    assert cache_is_valid(_write(tmp_path, fallback), EXPECTED)


def test_a_different_gpu_or_toolchain_forces_a_rebuild(tmp_path: Path) -> None:
    assert not cache_is_valid(_write(tmp_path, {**EXPECTED, "gpu_name": "NVIDIA T4"}), EXPECTED)
    assert not cache_is_valid(_write(tmp_path, {**EXPECTED, "trt_version": "10.0.0"}), EXPECTED)


def test_a_missing_engine_or_unreadable_sidecar_is_a_miss(tmp_path: Path) -> None:
    out = tmp_path / "yolo11s.engine"
    assert not cache_is_valid(out, EXPECTED)
    out.write_bytes(b"engine")
    out.with_suffix(out.suffix + ".json").write_text("{not json", encoding="utf-8")
    assert not cache_is_valid(out, EXPECTED)
