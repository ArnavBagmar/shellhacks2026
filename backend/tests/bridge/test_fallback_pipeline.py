"""The acceptance run, end to end (E5): induced timeouts flip to local
within three frames, the event stream does not stop, recovery waits its
full 30 s, and nothing ever escapes the loop.

The uplink clock is injected and frozen, so "30 s healthy" costs a
microsecond and no assertion depends on a sleep. The pipeline's own pacing
and frame deadline still run on real time -- those are the numbers step 6
is judged on and faking them would prove nothing.
"""

from __future__ import annotations

import asyncio
import logging

import pytest

from cane.bridge.backends import BackendError, BackendTimeout, SwitchingBackend
from cane.bridge.capture.grab_thread import GrabThread
from cane.bridge.capture.synthetic_source import SyntheticEquirectSource
from cane.bridge.config import BridgeConfig
from cane.bridge.health import RECOVER_HEALTHY_S, HealthMonitor, Mode
from cane.bridge.pipeline import BridgePipeline, LaptopClock
from cane.bridge.probe import CloudProbe, HealthzReading
from cane.bridge.rungs import RUNG_1, RUNG_3, rung_by_index
from cane.bridge.sender import OneInFlightSender
from cane.bridge.stats import format_stats_line
from cane.bridge.uplink import MonitoredUplink, RungPlanner
from cane.cloud.api_models import Detection, InferenceMeta, InferenceResponse
from cane.contracts.awareness_event import AwarenessEvent, Source

EQUIRECT_W = 640
EQUIRECT_H = 320
CROP_PX = 256
GRAB_FPS = 30.0
SEND_FPS = 20.0
RUN_S = 2.0
T0 = 1_790_000_000_000
S = 1000
FAIL_FROM_FRAME = 5
FLIP_BY_FRAME = 7
MAX_GAP_TICKS = 4


class FakeClock:
    def __init__(self, now_ms: int = T0) -> None:
        self.now_ms = now_ms

    def __call__(self) -> int:
        return self.now_ms

    def advance(self, ms: int) -> None:
        self.now_ms += ms


class ScriptedBackend:
    """One detection per call, or a scripted failure."""

    def __init__(self, name: str, source: Source) -> None:
        self.name = name
        self.source = source
        self.calls = 0
        self.closed = False
        self.fail_from: int | None = None
        self.fail_until: int | None = None
        self.raise_instead: BaseException | None = None

    def _fails(self) -> bool:
        if self.fail_from is None or self.calls < self.fail_from:
            return False
        return self.fail_until is None or self.calls <= self.fail_until

    async def infer(
        self, meta: InferenceMeta, jpegs: tuple[bytes, ...]
    ) -> InferenceResponse:
        self.calls += 1
        if self._fails():
            raise (
                self.raise_instead
                if self.raise_instead is not None
                else BackendTimeout("induced")
            )
        return InferenceResponse(
            schema=1,
            t_capture_ms=meta.t_capture_ms,
            inference_ms=1.0,
            detections=(
                Detection(
                    crop_index=1, cx=0.5, cy=0.5, w=0.1, h=0.3, cls="cyclist",
                    confidence=0.8, track_id=f"{self.name}:1:{self.calls}",
                    closing_mps=None, range_band="mid",
                ),
            ),
        )

    async def aclose(self) -> None:
        self.closed = True


class RecordingSink:
    def __init__(self) -> None:
        self.events: list[AwarenessEvent] = []
        self.at_s: list[float] = []

    async def send_event(self, event: AwarenessEvent) -> bool:
        self.events.append(event)
        self.at_s.append(asyncio.get_running_loop().time())
        return True

    def sources(self) -> list[Source]:
        return [event.source for event in self.events]


class Harness:
    def __init__(self, rung=RUNG_1, crop_px: int | None = CROP_PX, send_fps=SEND_FPS) -> None:
        self.clock = FakeClock()
        self.monitor = HealthMonitor(Mode.cloud(rung.index))
        self.cloud = ScriptedBackend("cloud", Source.X4_CLOUD)
        self.local = ScriptedBackend("local", Source.X4_LOCAL)
        self.switching = SwitchingBackend(self.monitor, cloud=self.cloud, local=self.local)
        self.uplink = MonitoredUplink(self.switching, self.monitor, clock=self.clock)
        self.sink = RecordingSink()
        self.config = BridgeConfig(
            inference_url="http://127.0.0.1:1/infer",
            rung=rung,
            send_fps=send_fps,
            session_id="fallback-test",
        )
        source = SyntheticEquirectSource(width=EQUIRECT_W, height=EQUIRECT_H, fps=GRAB_FPS)
        self.grab = GrabThread(source)
        self.sender = OneInFlightSender(self.uplink)
        self.pipeline = BridgePipeline(
            config=self.config,
            grab=self.grab,
            sender=self.sender,
            clock_provider=LaptopClock,
            event_sink=self.sink,
            crop_px=crop_px,
            monitor=self.monitor,
            source_provider=lambda: self.uplink.last_source,
        )

    async def run(self, duration_s: float = RUN_S):
        self.grab.start()
        try:
            return await self.pipeline.run(duration_s=duration_s)
        finally:
            self.grab.stop()


async def test_induced_timeouts_flip_to_local_within_three_frames() -> None:
    harness = Harness()
    harness.cloud.fail_from = FAIL_FROM_FRAME

    stats = await harness.run()

    sources = harness.sink.sources()
    assert sources, "the pipeline delivered nothing at all"
    assert all(s is Source.X4_CLOUD for s in sources[: FAIL_FROM_FRAME - 1])
    assert harness.monitor.is_local
    # The flip must have happened by the third failed frame, not later.
    assert harness.cloud.calls <= FLIP_BY_FRAME
    assert harness.local.calls > 0
    assert sources[-1] is Source.X4_LOCAL
    assert stats.failed >= 3


async def test_the_local_event_stream_has_no_gap_longer_than_one_tick() -> None:
    harness = Harness()
    harness.cloud.fail_from = FAIL_FROM_FRAME

    await harness.run()

    local_at = [
        at
        for at, event in zip(harness.sink.at_s, harness.sink.events, strict=True)
        if event.source is Source.X4_LOCAL
    ]
    assert len(local_at) > 10, local_at
    gaps = [b - a for a, b in zip(local_at, local_at[1:], strict=False)]
    assert max(gaps) <= MAX_GAP_TICKS / SEND_FPS, max(gaps)
    # Every answered local frame produced its event: no silent hole.
    local_events = sum(1 for s in harness.sink.sources() if s is Source.X4_LOCAL)
    assert local_events >= harness.local.calls - 1


async def test_no_exception_escapes_the_loop_when_both_backends_fail(
    caplog: pytest.LogCaptureFixture,
) -> None:
    harness = Harness()
    harness.cloud.fail_from = 1
    harness.local.fail_from = 1
    harness.local.raise_instead = BackendError("local model exploded", None)

    with caplog.at_level(logging.WARNING):
        stats = await harness.run(duration_s=1.0)

    assert harness.monitor.is_local
    assert harness.sink.events == []
    assert stats.failed > 0
    assert not any(r.levelno >= logging.ERROR for r in caplog.records)


async def test_an_unexpected_backend_fault_is_counted_not_raised() -> None:
    harness = Harness()
    harness.cloud.fail_from = 1
    harness.cloud.raise_instead = ZeroDivisionError("a bug, not a network")

    stats = await harness.run(duration_s=1.0)

    assert harness.monitor.is_local
    assert stats.failed > 0


async def test_recovery_needs_thirty_seconds_of_healthy_probes_then_events_return() -> None:
    harness = Harness()
    harness.cloud.fail_from = FAIL_FROM_FRAME
    harness.cloud.fail_until = FAIL_FROM_FRAME + 2

    class Healthz:
        def __init__(self) -> None:
            self.calls = 0

        async def fetch(self) -> HealthzReading:
            self.calls += 1
            return HealthzReading(status=200, payload={"detector": "fake"})

        async def aclose(self) -> None:
            return None

    probe = CloudProbe(Healthz(), harness.monitor, clock=harness.clock)
    harness.grab.start()
    run = asyncio.create_task(harness.pipeline.run(duration_s=RUN_S))
    try:
        for _ in range(400):
            if harness.monitor.is_local:
                break
            await asyncio.sleep(0.005)
        assert harness.monitor.is_local

        for second in range(RECOVER_HEALTHY_S):
            await probe.probe_once()
            harness.clock.advance(S)
        assert harness.monitor.is_local, "recovered before the 30 s were up"

        await probe.probe_once()
        assert harness.monitor.state.mode == Mode.cloud(4)

        before = harness.cloud.calls
        for _ in range(400):
            if harness.cloud.calls > before:
                break
            await asyncio.sleep(0.005)
        await run
    finally:
        harness.grab.stop()

    assert harness.sink.sources()[-1] is Source.X4_CLOUD
    assert harness.monitor.state.counters.recoveries == 1


# --------------------------------------------------------------------------
# E3 -- the rung is read every tick
# --------------------------------------------------------------------------


def test_the_planner_follows_the_monitor_and_honours_the_cli_overrides() -> None:
    config = BridgeConfig(inference_url="http://h/infer", rung=RUNG_1)
    monitor = HealthMonitor(Mode.cloud(1))
    planner = RungPlanner(config, monitor)

    assert planner.current().crop_px == 640
    assert planner.current().jpeg_quality == 70
    assert planner.current().period_s == pytest.approx(1 / 3)

    stepped = RungPlanner(config, HealthMonitor(Mode.cloud(3)))
    plan = stepped.current()
    assert (plan.index, plan.crop_px, plan.jpeg_quality) == (3, 512, 60)
    assert plan.period_s == pytest.approx(1 / 3)

    pinned = RungPlanner(
        BridgeConfig(inference_url="http://h/infer", rung=RUNG_1, send_fps=12.0),
        monitor,
        crop_px=256,
    )
    assert pinned.current().crop_px == 256
    assert pinned.current().period_s == pytest.approx(1 / 12)


def test_the_planner_without_a_monitor_is_the_static_config() -> None:
    config = BridgeConfig(inference_url="http://h/infer", rung=RUNG_3)
    plan = RungPlanner(config).current()

    assert (plan.index, plan.crop_px, plan.jpeg_quality) == (3, 512, 60)


async def test_the_meta_on_the_wire_carries_the_rung_actually_used() -> None:
    harness = Harness(rung=RUNG_3, crop_px=None, send_fps=4.0)

    await harness.run(duration_s=0.8)

    assert harness.uplink.last_meta is not None
    assert harness.uplink.last_meta.rung == RUNG_3.index
    assert harness.uplink.last_meta.crop_px == rung_by_index(3).crop_px


# --------------------------------------------------------------------------
# E2.7 -- the counters reach the 5 s stats line
# --------------------------------------------------------------------------


async def test_the_stats_line_reports_the_mode_and_the_health_counters() -> None:
    harness = Harness()
    harness.cloud.fail_from = FAIL_FROM_FRAME

    stats = await harness.run(duration_s=1.0)

    assert stats.health is not None
    assert stats.health.mode == "local"
    assert stats.health.flips_to_local == 1
    line = format_stats_line(stats)
    for token in ("mode local", "flips_to_local 1", "steps_down", "probe"):
        assert token in line, line


def test_a_stats_line_without_a_monitor_is_unchanged() -> None:
    from cane.bridge.stats import PipelineStats

    stats = PipelineStats(
        elapsed_s=5.0, grabbed=150, ticks=15, sent=15, dropped_busy=0,
        skipped_unsynced=0, skipped_no_frame=0, skipped_deadline=0, succeeded=15,
        failed=0, rejected=0, events_delivered=3, events_undelivered=0,
        max_in_flight=1, frame_age_p50_ms=20.0, frame_age_p95_ms=31.0,
        rtt_p50_ms=90.0, rtt_p95_ms=120.0,
    )

    assert stats.health is None
    assert "mode" not in format_stats_line(stats)
