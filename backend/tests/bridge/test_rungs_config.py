"""The four uplink rungs (design Sec 3.2) and the frozen bridge config."""

from __future__ import annotations

import dataclasses
import logging

import pytest

from cane.bridge.config import DELTA_DEG_MAX, DELTA_DEG_WARN, BridgeConfig
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
        ({"inference_url": "http://x/infer", "delta_deg": 45.0}, "delta_deg"),
        ({"inference_url": "http://x/infer", "send_fps": -1.0}, "send_fps"),
        ({"inference_url": "http://x/infer", "session_id": ""}, "session_id"),
    ],
)
def test_config_rejects_bad_values_with_a_named_message(
    kwargs: dict[str, object], message: str
) -> None:
    with pytest.raises(ValueError, match=message):
        BridgeConfig(**kwargs)  # type: ignore[arg-type]


def test_a_mount_offset_the_steering_arc_cannot_absorb_fails_at_startup() -> None:
    """Plan Sec 3.3 ruling 1: ``|delta_deg| > 30`` is a mounting or calibration
    error, and it must fail here -- once, at startup -- never as a per-frame
    steer drop. The message has to say so, because the person reading it is
    holding a camera that is on the wearer's shoulder crooked.
    """
    with pytest.raises(ValueError) as caught:
        BridgeConfig(inference_url="http://x/infer", delta_deg=45.0)

    message = str(caught.value)
    assert "delta_deg" in message
    assert str(int(DELTA_DEG_MAX)) in message
    assert "mount" in message


@pytest.mark.parametrize("delta_deg", [DELTA_DEG_MAX, -DELTA_DEG_MAX, 12.0, -25.0, 0.0])
def test_every_mount_offset_the_steering_law_supports_is_accepted(delta_deg: float) -> None:
    """The other half of ruling 1: a real shoulder offset is configuration, not
    an error. ``+-DELTA_DEG_MAX`` inclusive is exactly the range the ring can
    rotate through without any bearing folding past +-180.
    """
    config = BridgeConfig(inference_url="http://x/infer", delta_deg=delta_deg)

    assert config.delta_deg == delta_deg


@pytest.mark.parametrize("delta_deg", [DELTA_DEG_MAX + 0.5, -DELTA_DEG_MAX - 0.5])
def test_the_mount_offset_bound_is_inclusive_and_rejects_the_next_step(
    delta_deg: float,
) -> None:
    with pytest.raises(ValueError, match="delta_deg"):
        BridgeConfig(inference_url="http://x/infer", delta_deg=delta_deg)


# --------------------------------------------------------------------------
# Step 27 D: the blind wedge a legal mount offset costs, named at startup
# --------------------------------------------------------------------------


@pytest.mark.parametrize("delta_deg", [DELTA_DEG_WARN + 0.5, 20.0, -22.5, DELTA_DEG_MAX])
def test_a_large_but_legal_mount_offset_names_the_blind_wedge(
    delta_deg: float, caplog: pytest.LogCaptureFixture
) -> None:
    """Step 23's open judgement, closed. The ring covers +-90 in *camera*
    bearings, so an offset of N degrees leaves N degrees of the wearer's own
    forward arc unseen on one side -- at ``DELTA_DEG_MAX`` that is a third of
    that side. The hard failure stays at 30; below it the person straightening
    the camera is told the cost rather than left to infer it from a number.
    """
    with caplog.at_level(logging.WARNING, logger="cane.bridge.config"):
        config = BridgeConfig(inference_url="http://x/infer", delta_deg=delta_deg)

    assert config.delta_deg == delta_deg
    message = caplog.text
    assert f"{abs(delta_deg):.1f}" in message
    assert "unseen" in message
    assert ("left" if delta_deg > 0 else "right") in message


@pytest.mark.parametrize("delta_deg", [0.0, 1.875, 12.0, -15.0, DELTA_DEG_WARN])
def test_a_small_mount_offset_is_silent(
    delta_deg: float, caplog: pytest.LogCaptureFixture
) -> None:
    """The threshold is inclusive the quiet way: at exactly 15 degrees nothing
    is said, because a warning that fires on a normal mount is a warning
    nobody reads.
    """
    with caplog.at_level(logging.WARNING, logger="cane.bridge.config"):
        BridgeConfig(inference_url="http://x/infer", delta_deg=delta_deg)

    assert caplog.text == ""


def test_the_warning_threshold_is_below_the_hard_failure() -> None:
    assert 0.0 < DELTA_DEG_WARN < DELTA_DEG_MAX
    assert DELTA_DEG_WARN == 15.0
