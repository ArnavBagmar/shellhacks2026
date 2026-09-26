"""The tick sends one ``steer`` frame -- and in local fallback, none at all.

The acceptance that matters here is negative, so it is asserted as a *count*:
flipping to local must emit **zero** steer frames, not zero-valued ones
(plan Sec 4.2, step 8's no-silent-fake rule). A zero-steer frame is
indistinguishable from a clear corridor, and the phone would hold a
confident neutral cue while the bridge is blind.

The uplink clock is frozen and injected, like ``test_fallback_pipeline``'s,
so a flip costs no wall time.
"""

from __future__ import annotations

import pytest

from cane.bridge.backends import BackendTimeout, SwitchingBackend
from cane.bridge.capture.grab_thread import GrabThread
from cane.bridge.capture.synthetic_source import SyntheticEquirectSource
from cane.bridge.config import DELTA_DEG_MAX, BridgeConfig
from cane.bridge.gap_ring import DEPTH_CROP_INDICES, RING_COLUMNS
from cane.bridge.steer_frame import MAX_EMITTED_BEARING_ABS_DEG
from cane.bridge.health import HealthMonitor, Mode
from cane.bridge.pipeline import BridgePipeline, LaptopClock
from cane.bridge.rungs import RUNG_1
from cane.bridge.sender import OneInFlightSender
from cane.bridge.uplink import MonitoredUplink
from cane.cloud.api_models import (
    COLUMNS_PER_CROP,
    SCHEMA_VERSION,
    Detection,
    InferenceMeta,
    InferenceResponse,
)
from cane.contracts.awareness_event import AwarenessEvent, Source
from cane.transport.messages import Steer

from .conftest import depth_for, make_gap_profile, wall_plane_distances

EQUIRECT_W = 640
EQUIRECT_H = 320
CROP_PX = 256
GRAB_FPS = 30.0
SEND_FPS = 20.0
RUN_S = 1.0
T0 = 1_790_000_000_000
WALL_M = 0.40
FAIL_FROM_FRAME = 4


class FakeClock:
    def __init__(self, now_ms: int = T0) -> None:
        self.now_ms = now_ms

    def __call__(self) -> int:
        return self.now_ms


def _profiles(anchored: bool = True) -> tuple:
    distances = wall_plane_distances(WALL_M, side="left")
    assert len(distances) == RING_COLUMNS
    return tuple(
        make_gap_profile(
            crop_index,
            [
                depth_for(distances[block * COLUMNS_PER_CROP + column])
                for column in range(COLUMNS_PER_CROP)
            ],
            anchored=anchored,
        )
        for block, crop_index in enumerate(DEPTH_CROP_INDICES)
    )


class ScriptedBackend:
    """One detection per call, plus gap profiles when this backend has depth."""

    def __init__(
        self, name: str, source: Source, with_depth: bool, anchored: bool = True
    ) -> None:
        self.name = name
        self.source = source
        self.with_depth = with_depth
        self.anchored = anchored
        self.calls = 0
        self.fail_from: int | None = None

    async def infer(self, meta: InferenceMeta, jpegs: tuple[bytes, ...]) -> InferenceResponse:
        self.calls += 1
        if self.fail_from is not None and self.calls >= self.fail_from:
            raise BackendTimeout("induced")
        return InferenceResponse(
            schema=SCHEMA_VERSION,
            t_capture_ms=meta.t_capture_ms,
            inference_ms=1.0,
            detections=(
                Detection(
                    crop_index=1, cx=0.5, cy=0.5, w=0.1, h=0.3, cls="person",
                    confidence=0.8, track_id=f"{self.name}:{self.calls}",
                    range_band="near",
                ),
            ),
            gap_profiles=_profiles(self.anchored) if self.with_depth else (),
        )

    async def aclose(self) -> None:
        return None


class RecordingSink:
    """Records both streams in arrival order: the ordering is the assertion."""

    def __init__(self) -> None:
        self.events: list[AwarenessEvent] = []
        self.steers: list[Steer] = []
        self.log: list[str] = []

    async def send_event(self, event: AwarenessEvent) -> bool:
        self.events.append(event)
        self.log.append(f"event:{event.source.value}")
        return True

    async def send_steer(self, steer: Steer) -> bool:
        self.steers.append(steer)
        self.log.append("steer")
        return True


class Harness:
    def __init__(self, *, start_local: bool = False, local_has_depth: bool = False,
                 wire_steer: bool = True, anchored: bool = True) -> None:
        self.clock = FakeClock()
        self.monitor = HealthMonitor(
            Mode.local(RUNG_1.index) if start_local else Mode.cloud(RUNG_1.index)
        )
        self.cloud = ScriptedBackend(
            "cloud", Source.X4_CLOUD, with_depth=True, anchored=anchored
        )
        self.local = ScriptedBackend("local", Source.X4_LOCAL, with_depth=local_has_depth)
        self.switching = SwitchingBackend(self.monitor, cloud=self.cloud, local=self.local)
        self.uplink = MonitoredUplink(self.switching, self.monitor, clock=self.clock)
        self.sink = RecordingSink()
        self.config = BridgeConfig(
            inference_url="http://127.0.0.1:1/infer",
            rung=RUNG_1,
            send_fps=SEND_FPS,
            session_id="steer-test",
        )
        self.grab = GrabThread(
            SyntheticEquirectSource(width=EQUIRECT_W, height=EQUIRECT_H, fps=GRAB_FPS)
        )
        self.pipeline = BridgePipeline(
            config=self.config,
            grab=self.grab,
            sender=OneInFlightSender(self.uplink),
            clock_provider=LaptopClock,
            event_sink=self.sink,
            steer_sink=self.sink if wire_steer else None,
            crop_px=CROP_PX,
            monitor=self.monitor,
            source_provider=lambda: self.uplink.last_source,
        )

    async def run(self, duration_s: float = RUN_S):
        self.grab.start()
        try:
            return await self.pipeline.run(duration_s=duration_s)
        finally:
            self.grab.stop()


async def test_an_answered_cloud_tick_sends_exactly_one_steer_frame() -> None:
    harness = Harness()

    await harness.run()

    assert harness.sink.steers, "no steer frame reached the phone at all"
    assert len(harness.sink.steers) == len(harness.sink.events)
    assert harness.pipeline.steer_sent == len(harness.sink.steers)
    first = harness.sink.steers[0]
    assert first.steer > 0.0 and not first.blocked
    assert first.t_capture_ms == harness.sink.events[0].t_capture_ms


async def test_local_fallback_emits_zero_steer_frames() -> None:
    """Started in local mode: events flow, steer frames do not exist."""
    harness = Harness(start_local=True, local_has_depth=True)

    await harness.run()

    assert harness.sink.events, "the local path must keep warning the wearer"
    assert all(e.source is Source.X4_LOCAL for e in harness.sink.events)
    assert harness.sink.steers == []
    assert harness.pipeline.steer_sent == 0


async def test_no_steer_frame_arrives_after_the_flip_to_local() -> None:
    harness = Harness(local_has_depth=True)
    harness.cloud.fail_from = FAIL_FROM_FRAME

    await harness.run(duration_s=1.5)

    assert harness.monitor.is_local
    log = harness.sink.log
    assert "event:x4_local" in log
    after_flip = log[log.index("event:x4_local") :]
    assert "steer" not in after_flip, log
    assert harness.sink.steers, "the cloud half of the run did send steer frames"


async def test_the_detection_stream_is_untouched_without_a_steer_sink() -> None:
    harness = Harness(wire_steer=False)

    stats = await harness.run()

    assert harness.sink.events
    assert harness.sink.steers == []
    assert harness.pipeline.steer_sent == 0
    assert harness.pipeline.steer_undelivered > 0
    assert stats.events_delivered == len(harness.sink.events)


async def test_a_steer_sink_that_reports_failure_is_counted_not_raised() -> None:
    class RefusingSink(RecordingSink):
        async def send_steer(self, steer: Steer) -> bool:
            self.log.append("steer-refused")
            return False

    harness = Harness()
    harness.sink = RefusingSink()
    harness.pipeline._event_sink = harness.sink  # noqa: SLF001 - swapping the fake
    harness.pipeline._steer_sink = harness.sink  # noqa: SLF001

    await harness.run()

    assert harness.pipeline.steer_sent == 0
    assert harness.pipeline.steer_undelivered > 0
    assert "steer-refused" in harness.sink.log


@pytest.mark.parametrize(
    "delta_deg", [0.0, 1.5, -1.5, 12.0, -25.0, DELTA_DEG_MAX, -DELTA_DEG_MAX]
)
async def test_any_legal_mount_offset_still_sends_frames(delta_deg: float) -> None:
    """Plan Sec 3.3 ruling 1, end to end: a shoulder-worn X4 is several degrees
    off the wearer's axis, and every one of those degrees used to cost the
    whole steering stream (the invariant was asserted on the rotated ring, and
    the outer column centre is 88.125). No legitimate mount offset may ever
    cost a frame.
    """
    harness = Harness()
    harness.pipeline._config = BridgeConfig(  # noqa: SLF001 - one field, frozen elsewhere
        inference_url="http://127.0.0.1:1/infer",
        rung=RUNG_1,
        send_fps=SEND_FPS,
        session_id="steer-test",
        delta_deg=delta_deg,
    )

    await harness.run()

    assert harness.sink.steers
    assert all(
        abs(frame.gap_bearing_deg) <= MAX_EMITTED_BEARING_ABS_DEG
        for frame in harness.sink.steers
    )


async def test_the_pipeline_can_never_be_handed_an_out_of_range_mount_offset() -> None:
    """The per-frame path has no offset gate at all any more, so the bound has
    to hold one step earlier: ``delta_deg`` reaches ``steer_from_response``
    only through ``BridgeConfig``, which refuses a mounting error at startup.
    """
    with pytest.raises(ValueError, match="delta_deg"):
        BridgeConfig(
            inference_url="http://127.0.0.1:1/infer",
            rung=RUNG_1,
            send_fps=SEND_FPS,
            session_id="steer-test",
            delta_deg=45.0,
        )


async def test_the_live_unanchored_path_reaches_the_phone() -> None:
    """What will actually run: step 22 measured the ground fit rejected on
    every real crop, so the frames the phone sees carry
    ``proximity_anchored=false`` and a banded proximity.
    """
    harness = Harness(anchored=False)

    await harness.run()

    assert harness.sink.steers
    assert all(not frame.proximity_anchored for frame in harness.sink.steers)
    assert all(frame.proximity == 0.5 for frame in harness.sink.steers)
    assert all(frame.steer > 0.0 for frame in harness.sink.steers), "still steers right"
