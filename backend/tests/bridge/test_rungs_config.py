"""The four uplink rungs (design Sec 3.2) and the frozen bridge config."""

from __future__ import annotations

import dataclasses

import pytest

from cane.bridge.config import BridgeConfig
from cane.bridge.rungs import RUNG_1, RUNG_2, RUNG_3, RUNG_4, RUNGS, CROPS_PER_FRAME, rung_by_index

# (index, crop_px, jpeg_quality, fps, uplink Mbps) exactly as design Sec 3.2 prints it.
SPEC_TABLE = (
    (1, 640, 70, 3.0, 6.5),
    (2, 640, 70, 2.0, 4.3),
    (3, 512, 60, 3.0, 2.3),
    (4, 512, 60, 2.0, 1.5),
)
BITRATE_TOLERANCE_MBPS = 0.1


def test_rungs_match_the_design_table() -> None:
    assert len(RUNGS) == len(SPEC_TABLE)
    for rung, (index, crop_px, quality, fps, _mbps) in zip(RUNGS, SPEC_TABLE, strict=True):
        assert (rung.index, rung.crop_px, rung.jpeg_quality, rung.fps) == (
            index,
            crop_px,
            quality,
            fps,
        )
        assert rung.crops == CROPS_PER_FRAME == 6


def test_bitrate_estimate_reproduces_the_design_table() -> None:
    for rung, (_index, _px, _q, _fps, mbps) in zip(RUNGS, SPEC_TABLE, strict=True):
        assert rung.bitrate_estimate_mbps == pytest.approx(mbps, abs=BITRATE_TOLERANCE_MBPS)


def test_named_rungs_are_the_tuple_entries() -> None:
    assert (RUNG_1, RUNG_2, RUNG_3, RUNG_4) == RUNGS


def test_rung_by_index_and_its_error() -> None:
    assert rung_by_index(3) is RUNG_3
    with pytest.raises(ValueError, match="rung"):
        rung_by_index(5)


def test_rungs_are_frozen() -> None:
    with pytest.raises(dataclasses.FrozenInstanceError):
        RUNG_1.crop_px = 1  # type: ignore[misc]


def test_config_defaults_are_the_documented_ones() -> None:
    config = BridgeConfig(inference_url="http://127.0.0.1:8000/infer")

    assert config.rung is RUNG_1
    assert config.delta_deg == 0.0
    assert config.pipeline_delay_d_ms == 0
    assert config.allow_unsynced is False
    assert config.effective_send_fps == RUNG_1.fps
    assert config.session_id


def test_config_send_fps_override_wins_over_the_rung() -> None:
    config = BridgeConfig(inference_url="http://x/infer", send_fps=1.0)
    assert config.effective_send_fps == 1.0


def test_config_is_frozen() -> None:
    config = BridgeConfig(inference_url="http://x/infer")
    with pytest.raises(dataclasses.FrozenInstanceError):
        config.delta_deg = 5.0  # type: ignore[misc]


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"inference_url": "ftp://nope"}, "inference_url"),
        ({"inference_url": "http://x/infer", "request_timeout_s": 0.0}, "request_timeout_s"),
        ({"inference_url": "http://x/infer", "delta_deg": 400.0}, "delta_deg"),
        ({"inference_url": "http://x/infer", "send_fps": -1.0}, "send_fps"),
        ({"inference_url": "http://x/infer", "session_id": ""}, "session_id"),
    ],
)
def test_config_rejects_bad_values_with_a_named_message(
    kwargs: dict[str, object], message: str
) -> None:
    with pytest.raises(ValueError, match=message):
        BridgeConfig(**kwargs)  # type: ignore[arg-type]
