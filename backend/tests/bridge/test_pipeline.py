"""End to end on the loopback: synthetic X4 -> dewarp -> fake cloud -> phone.

The one thing this must prove is the chain of custody of the capture stamp:
the laptop converts arrival time to phone-clock time, the cloud echoes it
untouched, and that exact integer lands in the AwarenessEvent the phone
receives.
"""

from __future__ import annotations

import asyncio
import json
import time

import pytest
import websockets

from cane.bridge import pipeline as pipeline_module
from cane.bridge.capture.base import Frame
from cane.bridge.capture.grab_thread import GrabThread
from cane.bridge.capture.synthetic_source import (
    SyntheticEquirectSource,
    make_equirect_frame,
)
from cane.bridge.config import BridgeConfig
from cane.bridge.deadline import FRAME_DEADLINE_MS
from cane.bridge.pipeline import (
    BridgePipeline,
    LaptopClock,
    PipelineStats,
    format_stats_line,
)
from cane.bridge.rungs import RUNG_1
from cane.bridge.sender import OneInFlightSender
from cane.cloud.api_models import CROP_COUNT, InferenceRequest
from cane.contracts.awareness_event import Source
from cane.transport.clock_sync import ClockNotSyncedError
from cane.transport.messages import EventMessage, Ping, Pong, decode, encode
from cane.transport.server import EventLinkServer
from cane.transport.wall_clock import now_ms

EQUIRECT_W = 640
EQUIRECT_H = 320
CROP_PX = 256
GRAB_FPS = 30.0
SEND_FPS = 10.0
RUN_S = 1.2
RECV_TIMEOUT_S = 3.0
PHONE_OFFSET_MS = 250
DETECTION_CX = 0.75
# One stage slower than the whole frame deadline, and one comfortably inside it.
SLOW_STAGE_S = 0.15
FAST_STAGE_S = 0.005


class EchoingFakeCloud:
    """Answers with one detection in crop 1 and echoes the capture stamp."""

    def __init__(self, delay_s: float = 0.0) -> None:
        self._delay_s = delay_s
        self.requests: list[InferenceRequest] = []

    async def post_crops(self, request: InferenceRequest) -> bytes:
        if self._delay_s:
            await asyncio.sleep(self._delay_s)
        self.requests.append(request)
        return json.dumps(
            {
                "schema": 1,
                "t_capture_ms": request.meta.t_capture_ms,
                "inference_ms": 2.0,
                "detections": [
                    {
                        "crop_index": 1,
                        "cx": DETECTION_CX,
                        "cy": 0.5,
                        "w": 0.1,
                        "h": 0.3,
                        "cls": "cyclist",
                        "confidence": 0.77,
                        "track_id": "1:5",
                        "closing_mps": 4.0,
                        "range_band": "mid",
                    }
                ],
            }
        ).encode()

    async def aclose(self) -> None:
        return None


def _config(**patch: object) -> BridgeConfig:
    base: dict[str, object] = {
        "inference_url": "http://127.0.0.1:1/infer",
        "rung": RUNG_1,
        "send_fps": SEND_FPS,
        "session_id": "pipeline-test",
    }
    return BridgeConfig(**{**base, **patch})  # type: ignore[arg-type]


def _pipeline(
    transport: EchoingFakeCloud,
    config: BridgeConfig,
    clock_provider: object,
    sink: object | None,
    crop_px: int = CROP_PX,
) -> tuple[BridgePipeline, GrabThread]:
    source = SyntheticEquirectSource(width=EQUIRECT_W, height=EQUIRECT_H, fps=GRAB_FPS)
    grab = GrabThread(source)
    sender = OneInFlightSender(transport)
    pipeline = BridgePipeline(
        config=config,
        grab=grab,
        sender=sender,
        clock_provider=clock_provider,
        event_sink=sink,
        crop_px=crop_px,
    )
    return pipeline, grab


async def test_phone_receives_an_x4_cloud_event_with_the_echoed_stamp() -> None:
    transport = EchoingFakeCloud()
    async with EventLinkServer("127.0.0.1", 0, ping_interval_s=0.05) as server:
        async with websockets.connect(f"ws://127.0.0.1:{server.port}") as phone:
            ping = decode(await asyncio.wait_for(phone.recv(), RECV_TIMEOUT_S))
            assert isinstance(ping, Ping)
            phone_now = ping.t1 + PHONE_OFFSET_MS
            await phone.send(encode(Pong(t1=ping.t1, t2=phone_now, t3=phone_now)))
            for _ in range(50):
                if server.phone_clock.estimator.is_synced:
                    break
                await asyncio.sleep(0.02)
            assert server.phone_clock.estimator.is_synced

            pipeline, grab = _pipeline(
                transport, _config(), clock_provider=lambda: server.phone_clock, sink=server
            )
            grab.start()
            try:
                run = asyncio.create_task(pipeline.run(duration_s=RUN_S))

                event_msg = None
                while event_msg is None:
                    msg = decode(await asyncio.wait_for(phone.recv(), RECV_TIMEOUT_S))
                    if isinstance(msg, EventMessage):
                        event_msg = msg
                stats = await run
            finally:
                # A daemon grab thread left running keeps pushing 30 fps of
                # synthetic frames for the rest of the session and skews every
                # later timing test.
                grab.stop()

    event = event_msg.event
    assert event.source is Source.X4_CLOUD
    assert event.track_id == "1:5"
    assert event.range_m is None
    assert event.ttl_ms == 500  # cyclist
    assert event.bearing_deg == pytest.approx(60.0 + (DETECTION_CX - 0.5) * 60.0)
    sent_stamp = transport.requests[0].meta.t_capture_ms
    assert event.t_capture_ms == sent_stamp
    # The stamp really went through the phone clock, not the laptop clock.
    assert sent_stamp - PHONE_OFFSET_MS > 0
    assert stats.events_delivered >= 1
    assert stats.sent >= 1


async def test_six_crops_of_the_rung_size_reach_the_cloud() -> None:
    transport = EchoingFakeCloud()
    pipeline, grab = _pipeline(transport, _config(), clock_provider=LaptopClock, sink=None)
    grab.start()
    try:
        await pipeline.run(duration_s=RUN_S)
    finally:
        grab.stop()

    assert transport.requests
    request = transport.requests[0]
    assert len(request.crops) == CROP_COUNT
    assert all(crop.startswith(b"\xff\xd8") for crop in request.crops)  # JPEG SOI
    assert request.meta.crop_px == CROP_PX
    assert request.meta.rung == RUNG_1.index
    assert request.meta.session_id == "pipeline-test"
    assert request.meta.yaws_deg == (0.0, 60.0, 120.0, 180.0, -120.0, -60.0)


async def test_frame_age_at_send_stays_under_50ms_with_a_slow_cloud() -> None:
    transport = EchoingFakeCloud(delay_s=0.8)
    pipeline, grab = _pipeline(transport, _config(), clock_provider=LaptopClock, sink=None)
    grab.start()
    try:
        stats = await pipeline.run(duration_s=2.0)
    finally:
        grab.stop()

    assert stats.frame_age_p95_ms is not None
    assert stats.frame_age_p95_ms < 50
    assert stats.dropped_busy > 0
    assert stats.max_in_flight == 1
    assert stats.sent <= 4  # 2 s at one 0.8 s request at a time


async def test_an_unsynced_clock_skips_frames_instead_of_crashing() -> None:
    class NeverSynced:
        def capture_time_ms(self, t_arrival_laptop_ms: int) -> int:
            raise ClockNotSyncedError("no NTP sample yet")

    transport = EchoingFakeCloud()
    pipeline, grab = _pipeline(transport, _config(), clock_provider=NeverSynced, sink=None)
    grab.start()
    try:
        stats = await pipeline.run(duration_s=0.6)
    finally:
        grab.stop()

    assert transport.requests == []
    assert stats.skipped_unsynced > 0
    assert stats.sent == 0


class StalledGrab:
    """A camera that stopped delivering: the slot holds one old frame."""

    def __init__(self, age_ms: int) -> None:
        self.frames_grabbed = 1
        self._frame = Frame(
            image=make_equirect_frame(EQUIRECT_W, EQUIRECT_H, 0),
            t_arrival_laptop_ms=now_ms() - age_ms,
            seq=1,
        )

    def wait_for_new(self, last_seq: int, timeout_s: float) -> Frame | None:
        return self._frame if last_seq < self._frame.seq else None


async def test_a_stalled_camera_is_skipped_not_shipped_stale() -> None:
    """Sending a 500 ms old frame is exactly the failure this step exists to
    prevent, so the tick is dropped instead.
    """
    transport = EchoingFakeCloud()
    sender = OneInFlightSender(transport)
    pipeline = BridgePipeline(
        config=_config(),
        grab=StalledGrab(age_ms=500),
        sender=sender,
        clock_provider=LaptopClock,
        event_sink=None,
        crop_px=CROP_PX,
    )

    stats = await pipeline.run(duration_s=0.5)

    assert transport.requests == []
    assert stats.skipped_deadline > 0
    assert stats.sent == 0


async def test_an_overrun_tick_still_yields_to_the_event_loop() -> None:
    """The overrun branch must not return synchronously.

    ``try_send`` only creates the request task; that task first touches the
    wire when the loop next gets a scheduling slot. A pacing branch that
    returns without awaiting can therefore hold a finished frame back by a
    whole tick, which lands directly in frame age at send.
    """
    transport = EchoingFakeCloud()
    pipeline, _grab = _pipeline(transport, _config(), clock_provider=LaptopClock, sink=None)
    started: list[str] = []

    async def pending_request() -> None:
        started.append("ran")

    task = asyncio.get_running_loop().create_task(pending_request())
    overrun_due = time.monotonic() - 1.0

    next_due = await pipeline._sleep_until_next_tick(overrun_due, 1.0 / SEND_FPS)

    assert started == ["ran"], "the overrun branch returned without yielding"
    assert next_due <= time.monotonic()
    await task


def _slowed_dewarp(delay_s: float):
    """The real dewarp, but made to cost ``delay_s`` of the frame's budget."""
    real = pipeline_module.dewarp_equirect

    def slow(image: object, crop_px: int, t_arrival_laptop_ms: int) -> object:
        time.sleep(delay_s)
        return real(image, crop_px, t_arrival_laptop_ms)

    return slow


async def test_a_frame_that_misses_the_deadline_mid_tick_is_dropped(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Per-tick compute has a ceiling, and the ceiling is the frame's age.

    150 ms of dewarp means every frame is already older than the 100 ms
    deadline by the time the crops exist, so nothing must reach the wire:
    a warning about where a cyclist was is worse than no warning.
    """
    monkeypatch.setattr(pipeline_module, "dewarp_equirect", _slowed_dewarp(SLOW_STAGE_S))
    transport = EchoingFakeCloud()
    pipeline, grab = _pipeline(transport, _config(), clock_provider=LaptopClock, sink=None)
    grab.start()
    try:
        stats = await pipeline.run(duration_s=RUN_S)
    finally:
        grab.stop()

    assert SLOW_STAGE_S * 1000 > FRAME_DEADLINE_MS
    assert transport.requests == []
    assert stats.sent == 0
    assert stats.skipped_deadline > 0


async def test_a_frame_that_stays_inside_the_deadline_is_sent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(pipeline_module, "dewarp_equirect", _slowed_dewarp(FAST_STAGE_S))
    transport = EchoingFakeCloud()
    pipeline, grab = _pipeline(transport, _config(), clock_provider=LaptopClock, sink=None)
    grab.start()
    try:
        stats = await pipeline.run(duration_s=RUN_S)
    finally:
        grab.stop()

    assert transport.requests
    assert stats.sent > 0
    assert stats.skipped_deadline == 0


async def test_laptop_clock_applies_the_pipeline_delay_constant() -> None:
    clock = LaptopClock(pipeline_delay_d_ms=40)
    assert clock.capture_time_ms(1_790_000_000_000) == 1_790_000_000_000 - 40


def _stats(**patch: object) -> PipelineStats:
    base: dict[str, object] = {
        "elapsed_s": 5.0,
        "grabbed": 150,
        "ticks": 15,
        "sent": 15,
        "dropped_busy": 0,
        "skipped_unsynced": 0,
        "skipped_no_frame": 0,
        "skipped_deadline": 0,
        "succeeded": 15,
        "failed": 0,
        "rejected": 0,
        "events_delivered": 3,
        "events_undelivered": 0,
        "max_in_flight": 1,
        "frame_age_p50_ms": 20.0,
        "frame_age_p95_ms": 31.0,
        "rtt_p50_ms": 90.0,
        "rtt_p95_ms": 120.0,
    }
    return PipelineStats(**{**base, **patch})  # type: ignore[arg-type]


def test_the_stats_line_names_every_acceptance_number() -> None:
    line = format_stats_line(_stats())

    for token in ("grab", "send", "dropped_busy", "frame_age", "rtt", "p50", "p95", "deadline"):
        assert token in line


def test_the_stats_line_reports_what_never_reached_the_phone() -> None:
    """An event produced and not delivered is the silent failure this line
    exists for: `events 3` alone reads like success.
    """
    line = format_stats_line(_stats(events_delivered=3, events_undelivered=7))

    assert "events 3 undelivered 7" in line


def test_the_stats_line_names_the_local_detector_and_the_ignored_frames() -> None:
    line = format_stats_line(_stats(local_detector="ultralytics", frames_ignored=4))

    assert "local=ultralytics" in line
    assert "frames_ignored 4" in line


def test_a_run_with_no_local_detector_says_nothing_about_one() -> None:
    assert "local=" not in format_stats_line(_stats())
