"""`CloudSettings` (A3): the env names the container and the laptop share.

The one trap worth a dedicated file: a pydantic field whose name starts with
`model_` emits a `protected_namespaces` UserWarning, which is a hard failure
under `-W error`. Hence `weights_path`, never `model_path`.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from cane.cloud.settings import CloudSettings

EXPECTED_DEFAULTS = {
    "detector": "fake",
    "weights_path": Path("yolo11s.pt"),
    "engine_path": Path("/models/yolo11s_b12dyn_fp16.engine"),
    "device": "cpu",
    "conf_detect": 0.15,
    "conf_emit": 0.35,
    "iou": 0.45,
    "max_det_per_crop": 20,
    "max_crop_bytes": 2_000_000,
    "max_sessions": 8,
    "api_key": "",
    "port": 8000,
    "log_level": "INFO",
}


@pytest.fixture(autouse=True)
def _no_cane_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """A developer's shell must not change what these tests assert."""
    import os

    for key in [k for k in os.environ if k.startswith("CANE_")]:
        monkeypatch.delenv(key, raising=False)


def test_defaults_match_the_amendment_table() -> None:
    settings = CloudSettings()
    for field, expected in EXPECTED_DEFAULTS.items():
        assert getattr(settings, field) == expected, field


def test_no_field_name_starts_with_model_() -> None:
    """`model_path` would warn on class construction, not at first use."""
    assert not [f for f in CloudSettings.model_fields if f.startswith("model_")]


def test_env_overrides_use_the_cane_prefix(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CANE_DETECTOR", "ultralytics")
    monkeypatch.setenv("CANE_DEVICE", "mps")
    monkeypatch.setenv("CANE_CONF_EMIT", "0.5")
    monkeypatch.setenv("CANE_MAX_CROP_BYTES", "4096")
    settings = CloudSettings()
    assert settings.detector == "ultralytics"
    assert settings.device == "mps"
    assert settings.conf_emit == 0.5
    assert settings.max_crop_bytes == 4096


def test_an_unknown_detector_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CANE_DETECTOR", "yolov3")
    with pytest.raises(ValidationError):
        CloudSettings()


def test_settings_are_frozen() -> None:
    settings = CloudSettings()
    with pytest.raises(ValidationError):
        settings.device = "mps"  # type: ignore[misc]


def test_unrelated_env_is_ignored(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CANE_SOMETHING_WE_NEVER_DEFINED", "1")
    assert CloudSettings().detector == "fake"


def test_detect_floor_is_below_the_emit_threshold() -> None:
    """The BYTE half of ByteTrack: 0.15-0.35 boxes cannot start a track but
    keep an existing one alive through a blurry frame (A8).
    """
    settings = CloudSettings()
    assert settings.conf_detect < settings.conf_emit


def test_dump_dir_is_off_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    assert CloudSettings().dump_dir is None
    monkeypatch.setenv("CANE_DUMP_DIR", "/tmp/cane-dump")
    assert CloudSettings().dump_dir == Path("/tmp/cane-dump")
