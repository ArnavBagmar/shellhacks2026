"""End to end on the loopback: synthetic X4 -> dewarp -> fake cloud -> phone.

The one thing this must prove is the chain of custody of the capture stamp:
the laptop converts arrival time to phone-clock time, the cloud echoes it
untouched, and that exact integer lands in the AwarenessEvent the phone
receives.
"""

from __future__ import annotations

import asyncio
import json

import pytest
import websockets

from cane.bridge.capture.grab_thread import GrabThread
from cane.bridge.capture.synthetic_source import SyntheticEquirectSource
from cane.bridge.config import BridgeConfig
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

EQUIRECT_W = 640
EQUIRECT_H = 320
CROP_PX = 256
GRAB_FPS = 30.0
SEND_FPS = 10.0
RUN_S = 1.2
RECV_TIMEOUT_S = 3.0
PHONE_OFFSET_MS = 250
DETECTION_CX = 0.75


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
            run = asyncio.create_task(pipeline.run(duration_s=RUN_S))

            event_msg = None
            while event_msg is None:
                msg = decode(await asyncio.wait_for(phone.recv(), RECV_TIMEOUT_S))
                if isinstance(msg, EventMessage):
                    event_msg = msg
            stats = await run

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

    await pipeline.run(duration_s=RUN_S)

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

    stats = await pipeline.run(duration_s=2.0)

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

    stats = await pipeline.run(duration_s=0.6)

    assert transport.requests == []
    assert stats.skipped_unsynced > 0
    assert stats.sent == 0


async def test_laptop_clock_applies_the_pipeline_delay_constant() -> None:
    clock = LaptopClock(pipeline_delay_d_ms=40)
    assert clock.capture_time_ms(1_790_000_000_000) == 1_790_000_000_000 - 40


def test_the_stats_line_names_every_acceptance_number() -> None:
    line = format_stats_line(
        PipelineStats(
            elapsed_s=5.0,
            grabbed=150,
            ticks=15,
            sent=15,
            dropped_busy=0,
            skipped_unsynced=0,
            skipped_no_frame=0,
            succeeded=15,
            failed=0,
            rejected=0,
            events_delivered=3,
            events_undelivered=0,
            max_in_flight=1,
            frame_age_p50_ms=20.0,
            frame_age_p95_ms=31.0,
            rtt_p50_ms=90.0,
            rtt_p95_ms=120.0,
        )
    )

    for token in ("grab", "send", "dropped_busy", "frame_age", "rtt", "p50", "p95"):
        assert token in line
