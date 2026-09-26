"""`CANE_DEPTH_*`: the knobs, and the one decision they encode.

The depth head is **on by default** (`CANE_DEPTH_ENABLED=true`) because the
tier-2 deploy files -- `docker/Dockerfile`, `deploy/startup.sh` -- were not in
step 22's scope, so there is no new env var on the L4 to forget to set. Which
*backend* it runs then follows the detector's posture: a process serving the
scripted detector has no model in it at all, and must not import torch to find
that out.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from cane.cloud.depth.constants import (
    DEFAULT_DEPTH_ENGINE_PATH,
    DEFAULT_DEPTH_REPO,
    DEFAULT_DEPTH_WEIGHTS,
    DEFAULT_INPUT_SIZE,
    DEFAULT_MAX_GROUND_RESIDUAL,
    INPUT_SIZE_MULTIPLE_OF,
)
from cane.cloud.depth.factory import build_depth_head, load_with_fallback, resolve_backend
from cane.cloud.settings import CloudSettings

EXPECTED_DEPTH_DEFAULTS = {
    "depth_enabled": True,
    "depth_backend": None,
    "depth_input_size": DEFAULT_INPUT_SIZE,
    "depth_device": "",
    "depth_repo": DEFAULT_DEPTH_REPO,
    "depth_weights_path": DEFAULT_DEPTH_WEIGHTS,
    "depth_engine_path": DEFAULT_DEPTH_ENGINE_PATH,
    "depth_max_ground_residual": DEFAULT_MAX_GROUND_RESIDUAL,
}


@pytest.fixture(autouse=True)
def _no_cane_env(monkeypatch: pytest.MonkeyPatch) -> None:
    import os

    for key in [k for k in os.environ if k.startswith("CANE_")]:
        monkeypatch.delenv(key, raising=False)


def settings(**overrides: object) -> CloudSettings:
    return CloudSettings(**overrides)  # type: ignore[arg-type]


def test_the_depth_defaults_are_the_documented_ones() -> None:
    built = settings()
    for field, expected in EXPECTED_DEPTH_DEFAULTS.items():
        assert getattr(built, field) == expected, field


def test_no_depth_field_starts_with_model_() -> None:
    """Same trap as `weights_path`: a `model_*` field warns on class creation,
    and the suite runs warnings as errors.
    """
    assert not [f for f in CloudSettings.model_fields if f.startswith("model_")]


def test_the_input_size_default_is_the_measured_one() -> None:
    """280 is what `deploy/depth_crop_timing.py` measured at 31.8 ms p50 per
    640-px crop on MPS (plan Sec 4.5), and it must stay a multiple of 14: the
    official transform rounds to a multiple of the ViT patch size, so any
    other value silently changes the shape the engine was built for.
    """
    assert DEFAULT_INPUT_SIZE == 280
    assert INPUT_SIZE_MULTIPLE_OF == 14
    assert DEFAULT_INPUT_SIZE % INPUT_SIZE_MULTIPLE_OF == 0
    with pytest.raises(ValidationError):
        settings(depth_input_size=281)


def test_the_checkpoint_paths_are_the_benchmarks_own() -> None:
    """Step 22 reuses `cane.benchmarks.depth_model`'s official-revision loader,
    so it must point at the same repo and checkpoint. Duplicated constants
    would let the two drift; this pins them together without making
    `settings.py` import the benchmark (which pulls in a clock-reading CLI).
    """
    from cane.benchmarks.depth_model import DEFAULT_REPO, DEFAULT_WEIGHTS

    assert DEFAULT_DEPTH_REPO == DEFAULT_REPO
    assert DEFAULT_DEPTH_WEIGHTS == DEFAULT_WEIGHTS


def test_env_overrides_use_the_cane_depth_prefix(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CANE_DEPTH_ENABLED", "false")
    monkeypatch.setenv("CANE_DEPTH_BACKEND", "tensorrt")
    monkeypatch.setenv("CANE_DEPTH_INPUT_SIZE", "518")
    monkeypatch.setenv("CANE_DEPTH_DEVICE", "mps")
    monkeypatch.setenv("CANE_DEPTH_ENGINE_PATH", "/models/depth.engine")
    monkeypatch.setenv("CANE_DEPTH_MAX_GROUND_RESIDUAL", "0.4")
    built = CloudSettings()
    assert built.depth_enabled is False
    assert built.depth_backend == "tensorrt"
    assert built.depth_input_size == 518
    assert built.depth_device == "mps"
    assert built.depth_engine_path == Path("/models/depth.engine")
    assert built.depth_max_ground_residual == 0.4


def test_an_unknown_depth_backend_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CANE_DEPTH_BACKEND", "midas")
    with pytest.raises(ValidationError):
        CloudSettings()


def test_the_residual_threshold_stays_a_ratio() -> None:
    for bad in (-0.1, 0.0):
        with pytest.raises(ValidationError):
            settings(depth_max_ground_residual=bad)


# --------------------------------------------------------------------------
# Which backend, and what it costs to decide
# --------------------------------------------------------------------------


def test_the_backend_follows_the_detectors_posture() -> None:
    assert resolve_backend(settings(detector="fake")) == "off"
    assert resolve_backend(settings(detector="ultralytics")) == "torch"
    assert resolve_backend(settings(detector="tensorrt")) == "tensorrt"


def test_an_explicit_backend_wins_over_the_posture() -> None:
    assert resolve_backend(settings(detector="fake", depth_backend="torch")) == "torch"
    assert resolve_backend(settings(detector="tensorrt", depth_backend="off")) == "off"
    assert resolve_backend(settings(detector="ultralytics", depth_backend="fake")) == "fake"


def test_disabling_depth_beats_everything() -> None:
    """One knob to turn the whole head off at the venue, whatever else is set."""
    for detector in ("fake", "ultralytics", "tensorrt"):
        for backend in (None, "torch", "tensorrt", "fake"):
            built = settings(detector=detector, depth_backend=backend, depth_enabled=False)
            assert resolve_backend(built) == "off"
            assert build_depth_head(built) is None


def test_the_default_posture_builds_no_head_and_imports_nothing_heavy() -> None:
    """The laptop's dev posture and every existing cloud test: no depth model
    in the process, and `sys.modules` proves torch was never reached.
    """
    import sys

    assert build_depth_head(settings()) is None
    assert {"torch", "ultralytics"} & set(sys.modules) == set()


def test_a_configured_head_is_built_lazily_without_importing_torch() -> None:
    """Constructing the head must not load the model: `/warmup` decides when
    the 5-10 s of MPS graph compilation happens, not module import.
    """
    import sys

    head = build_depth_head(settings(detector="fake", depth_backend="torch"))
    assert head is not None and head.label == "torch"
    assert head.ready is False
    assert "torch" not in sys.modules


def test_the_fake_backend_builds_a_scripted_head() -> None:
    head = build_depth_head(settings(depth_backend="fake"))
    assert head is not None and head.label == "fake"
    assert head.warm(256) is not None
    assert head.ready is True


def test_the_head_carries_the_configured_residual_threshold() -> None:
    head = build_depth_head(settings(depth_backend="fake", depth_max_ground_residual=0.9))
    assert head is not None
    assert head.max_residual == 0.9


# --------------------------------------------------------------------------
# TensorRT with a torch fallback (plan step 22; the L4 half is UNVERIFIED)
# --------------------------------------------------------------------------


def test_the_tensorrt_loader_falls_back_to_torch() -> None:
    """`export_engine.py` does the same for the detector: the documented
    escape hatch is a slower path that works, never a dead service.
    """
    calls: list[str] = []

    def primary() -> Any:
        calls.append("trt")
        raise RuntimeError("no engine at /models/depth.engine")

    def fallback() -> Any:
        calls.append("torch")
        return "torch-estimator"

    assert load_with_fallback(primary, fallback) == "torch-estimator"
    assert calls == ["trt", "torch"]


def test_a_working_primary_is_not_second_guessed() -> None:
    def primary() -> Any:
        return "trt-estimator"

    def fallback() -> Any:  # pragma: no cover - must never run
        raise AssertionError("the fallback ran with a healthy engine")

    assert load_with_fallback(primary, fallback) == "trt-estimator"


def test_both_paths_failing_raises_the_fallbacks_error() -> None:
    """The head turns this into absence; what must not happen is a silent
    success that leaves the caller with nothing to log.
    """

    def primary() -> Any:
        raise RuntimeError("trt gone")

    def fallback() -> Any:
        raise RuntimeError("torch gone too")

    with pytest.raises(RuntimeError, match="torch gone too"):
        load_with_fallback(primary, fallback)
