"""``python -m cane.bridge`` argument parsing and source selection."""

from __future__ import annotations

from pathlib import Path

import pytest

from cane.bridge.__main__ import build_config, open_source, parse_args
from cane.bridge.capture.opencv_source import SourceOpenError
from cane.bridge.capture.synthetic_source import SyntheticEquirectSource
from cane.bridge.rungs import RUNG_1, RUNG_3

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
