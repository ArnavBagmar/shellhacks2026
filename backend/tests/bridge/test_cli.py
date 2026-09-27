"""``python -m cane.bridge`` argument parsing and source selection."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from cane.bridge.__main__ import SceneHook, build_config, build_scene_vlm, open_source, parse_args
from cane.bridge.capture.opencv_source import SourceOpenError
from cane.bridge.capture.synthetic_source import SyntheticEquirectSource
from cane.bridge.rungs import RUNG_1, RUNG_3
from cane.scene.frames import StillProvider
from cane.scene.service import SceneQueryService
from cane.scene.vlm import FakeVLM, VlmUnavailableError
from cane.transport.messages import SceneQuery

INFERENCE_URL = "http://127.0.0.1:8000/infer"


def test_defaults_are_the_documented_run() -> None:
    args = parse_args(["--inference-url", INFERENCE_URL])
    config = build_config(args)

    assert config.source_spec == "synthetic"
    assert config.rung is RUNG_1
    assert config.ws_port == 8765
    assert config.allow_unsynced is False
    assert config.delta_deg == 0.0


def test_flags_are_threaded_into_the_frozen_config() -> None:
    args = parse_args(
        [
            "--source",
            "x4",
            "--rung",
            "3",
            "--inference-url",
            INFERENCE_URL,
            "--ws-port",
            "9999",
            "--delta-deg",
            "12.5",
            "--pipeline-delay-ms",
            "40",
            "--allow-unsynced",
            "--session-id",
            "walk-1",
        ]
    )
    config = build_config(args)

    assert config.source_spec == "x4"
    assert config.rung is RUNG_3
    assert config.ws_port == 9999
    assert config.delta_deg == 12.5
    assert config.pipeline_delay_d_ms == 40
    assert config.allow_unsynced is True
    assert config.session_id == "walk-1"


def test_an_invalid_rung_is_rejected_by_the_parser() -> None:
    with pytest.raises(SystemExit):
        parse_args(["--rung", "9", "--inference-url", INFERENCE_URL])


def test_synthetic_source_spec_opens_a_synthetic_source() -> None:
    source = open_source("synthetic")
    try:
        assert isinstance(source, SyntheticEquirectSource)
        assert source.frame_size == (2880, 1440)
    finally:
        source.close()


def test_a_missing_file_spec_is_a_clear_error(tmp_path: Path) -> None:
    with pytest.raises(SourceOpenError, match="not found"):
        open_source(f"file:{tmp_path / 'nope.mp4'}")


def test_an_unknown_source_spec_names_the_valid_ones() -> None:
    with pytest.raises(ValueError, match="synthetic"):
        open_source("webcam-please")


# --------------------------------------------------------------------------
# Step 8: the local fallback's own flags
# --------------------------------------------------------------------------


def test_the_fallback_flags_default_to_cloud_and_an_auto_detector() -> None:
    args = parse_args(["--inference-url", INFERENCE_URL])

    assert args.start_mode == "cloud"
    assert args.local_detector is None
    assert args.local_weights is None


def test_start_mode_local_starts_the_monitor_on_the_local_backend() -> None:
    from cane.bridge.__main__ import build_monitor

    args = parse_args(["--inference-url", INFERENCE_URL, "--start-mode", "local", "--rung", "3"])
    monitor = build_monitor(args)

    assert monitor.is_local is True
    assert monitor.rung.index == 3


def test_the_default_start_mode_is_the_configured_rung_on_the_cloud() -> None:
    from cane.bridge.__main__ import build_monitor

    monitor = build_monitor(parse_args(["--inference-url", INFERENCE_URL, "--rung", "2"]))

    assert monitor.is_local is False
    assert monitor.rung.index == 2


def test_an_unknown_local_detector_is_rejected_by_the_parser() -> None:
    with pytest.raises(SystemExit):
        parse_args(["--inference-url", INFERENCE_URL, "--local-detector", "coreml"])


def test_the_local_detector_flags_reach_the_settings() -> None:
    from cane.bridge.backends import local_settings

    settings = local_settings("fake", "/tmp/yolo11s.mlpackage")

    assert settings.detector == "fake"
    assert str(settings.weights_path) == "/tmp/yolo11s.mlpackage"
    assert settings.device == "mps"


class _ExplodingLoader:
    """Stands in for `LocalBackend` when the weights will not load."""

    @staticmethod
    def build(*args: object, **kwargs: object) -> object:
        raise RuntimeError("no torch on this machine")


def test_a_local_detector_that_cannot_load_stops_the_bridge(
    caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No silent fake: a fallback that is not the detector the operator
    asked for would flip in at the worst moment and detect nothing, and
    the only sign would be an empty event stream.
    """
    import logging

    from cane.bridge import __main__ as main_module

    monkeypatch.setattr(main_module, "LocalBackend", _ExplodingLoader)
    args = parse_args(["--inference-url", INFERENCE_URL, "--local-detector", "ultralytics"])

    with caplog.at_level(logging.ERROR, logger="cane.bridge"):
        with pytest.raises(SystemExit) as excinfo:
            main_module.build_local_backend(args)

    assert excinfo.value.code not in (0, None)
    messages = [r.getMessage() for r in caplog.records if r.levelno >= logging.ERROR]
    assert any("local detector" in m and "ultralytics" in m for m in messages)


def test_an_explicit_fake_detector_is_built_and_named_by_the_banner(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The banner reports the detector that was *built*, not the flags."""
    import logging

    from cane.bridge.__main__ import _log_banner, build_local_backend

    args = parse_args(["--inference-url", INFERENCE_URL, "--local-detector", "fake"])
    backend = build_local_backend(args)

    assert backend.detector_name == backend.service.detector.name == "fake"

    with caplog.at_level(logging.INFO, logger="cane.bridge"):
        _log_banner(build_config(args), args, backend)

    assert "fallback=fake" in caplog.records[-1].getMessage()


def test_the_local_backend_is_built_with_the_configured_local_budget() -> None:
    """`BridgeConfig.local_timeout_s` is the knob; a default that is only
    ever the default is not configurable at all.
    """
    from cane.bridge.__main__ import build_local_backend
    from cane.bridge.config import BridgeConfig

    args = parse_args(["--inference-url", INFERENCE_URL, "--local-detector", "fake"])
    config = BridgeConfig(inference_url=INFERENCE_URL, local_timeout_s=0.5)

    assert build_local_backend(args, timeout_s=config.local_timeout_s).timeout_s == 0.5
    assert build_local_backend(args).timeout_s == BridgeConfig(
        inference_url=INFERENCE_URL
    ).local_timeout_s


def test_the_request_timeout_default_is_the_one_second_the_monitor_judges_on() -> None:
    from cane.bridge.backends import DEFAULT_BACKEND_TIMEOUT_S
    from cane.bridge.config import DEFAULT_REQUEST_TIMEOUT_S

    # One number, not two that happen to agree today (F8).
    assert DEFAULT_REQUEST_TIMEOUT_S is DEFAULT_BACKEND_TIMEOUT_S
    assert DEFAULT_REQUEST_TIMEOUT_S == 1.0
    config = build_config(parse_args(["--inference-url", INFERENCE_URL]))
    assert config.request_timeout_s == 1.0


def test_the_health_hook_converts_the_phone_clock_before_the_monitor_sees_it() -> None:
    from cane.bridge.__main__ import PhoneHealthHook
    from cane.bridge.health import HealthMonitor, Mode
    from cane.transport.messages import PhoneHealth
    from cane.transport.wall_clock import now_ms

    monitor = HealthMonitor(Mode.cloud(1))
    hook = PhoneHealthHook(monitor)

    assert hook.offset_ms() == 0  # no phone, no handshake, no offset
    hook(
        PhoneHealth(
            t_phone_ms=now_ms(),
            window_ms=10_000,
            received=10,
            stale_dropped=5,
            stale_drop_rate=0.5,
            complete_buckets=4,
        )
    )

    assert monitor.is_local is True
    assert monitor.state.counters.flips_to_local == 1


def test_scene_is_off_by_default_and_builds_nothing() -> None:
    args = parse_args([])
    assert args.scene is False
    assert build_scene_vlm(args) is None


def test_scene_without_a_key_is_a_clear_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    with pytest.raises(VlmUnavailableError, match="GEMINI_API_KEY"):
        build_scene_vlm(parse_args(["--scene"]))


async def test_the_scene_hook_sends_the_answer_to_the_phone() -> None:
    sent = []

    class FakeServer:
        async def send_answer(self, answer):
            sent.append(answer)
            return True

    service = SceneQueryService(
        FakeVLM(), StillProvider(np.zeros((32, 64, 3), np.uint8)), settle_s=0.0, crop_px=16
    )
    hook = SceneHook(service)
    hook.server = FakeServer()  # type: ignore[assignment]
    await hook(SceneQuery(query_id="q-1", kind="find", target="door", utterance="find a door",
                          t_phone_ms=1_790_000_000_000, phone_frame=None))

    assert [a.status for a in sent] == ["not_found"]


def test_recording_is_off_unless_asked_for() -> None:
    """No flag, no recorder: the measured run is the unmeasured run."""
    args = parse_args(["--inference-url", INFERENCE_URL])

    assert build_config(args).record_events is None


def test_record_events_is_threaded_into_the_frozen_config(tmp_path: Path) -> None:
    target = tmp_path / "walk-a.jsonl"

    args = parse_args(["--inference-url", INFERENCE_URL, "--record-events", str(target)])

    assert build_config(args).record_events == target
