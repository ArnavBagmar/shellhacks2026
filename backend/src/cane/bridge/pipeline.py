"""The bridge loop: grab -> dewarp -> encode -> send -> events -> phone.

Paced at the rung's fps. Every tick takes the *newest* frame (rule 1) and
sends only if the uplink slot is free (rule 2); both of those failing silently
is what design Sec 3.3 warns about, so the 5-second stats line prints the
numbers that would expose it: grab fps, send fps, dropped_busy, frame age at
send p50/p95, request RTT p50/p95.

``cv2`` work runs in ``asyncio.to_thread`` so the event loop is never blocked
while a request is in flight.
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass
from enum import StrEnum
from typing import Callable, Protocol

from cane.bridge.capture.base import Frame
from cane.bridge.capture.grab_thread import GrabThread
from cane.bridge.config import BridgeConfig
from cane.bridge.dewarp import CROP_YAWS_DEG, encode_cropset, dewarp_equirect
from cane.bridge.events import events_from_response
from cane.bridge.sender import OneInFlightSender, SendOutcome
from cane.cloud.api_models import InferenceMeta, InferenceRequest, InferenceResponse
from cane.contracts.awareness_event import AwarenessEvent
from cane.transport.clock_sync import ClockNotSyncedError
from cane.transport.wall_clock import now_ms

logger = logging.getLogger(__name__)

FRAME_WAIT_TIMEOUT_S = 0.25
UNSYNCED_LOG_INTERVAL_S = 5.0
# A 30 fps camera means the frame sitting in the slot is on average half a
# frame period (17 ms) old, and up to 33 ms old, before we have done any work.
# When the one already there is staler than this, wait for the next arrival
# instead: the send happens a few ms later but carries pixels from a later
# instant of reality, which is strictly better for a warning system -- and it
# is what keeps frame age at send well inside the 50 ms budget.
MAX_FRAME_AGE_AT_PICKUP_MS = 10
# If even after waiting there is nothing fresher than this, the camera has
# stalled: skip the tick rather than ship a stale frame. Dropping beats
# sending a warning about where a cyclist was a third of a second ago, and it
# is the same trade as the one-request-in-flight rule, applied to capture.
MAX_SENDABLE_FRAME_AGE_MS = 100


class TickSkip(StrEnum):
    """Why a tick produced no request. Counted on the loop thread."""

    NO_FRAME = "no_frame"
    STALE_FRAME = "stale_frame"
    UNSYNCED = "unsynced"
    ENCODE_FAILED = "encode_failed"


@dataclass(frozen=True, eq=False)
class TickWork:
    """What one worker-thread hop produced: a request, or a reason it did not."""

    skip: TickSkip | None
    request: InferenceRequest | None = None
    frame_seq: int = 0
    t_arrival_laptop_ms: int = 0


class CaptureClock(Protocol):
    """Converts laptop arrival time into the stamp the phone understands."""

    def capture_time_ms(self, t_arrival_laptop_ms: int) -> int: ...


class EventSink(Protocol):
    """``EventLinkServer`` satisfies this; tests pass a fake."""

    async def send_event(self, event: AwarenessEvent) -> bool: ...


@dataclass(frozen=True)
class LaptopClock:
    """``--allow-unsynced``: stamp with the laptop clock, offset 0.

    Only correct when no phone is connected (bench runs, the live acceptance
    test). With a phone present the events would be timestamped in the wrong
    clock domain, which is why the flag logs a loud warning.
    """

    pipeline_delay_d_ms: int = 0

    def capture_time_ms(self, t_arrival_laptop_ms: int) -> int:
        return t_arrival_laptop_ms - self.pipeline_delay_d_ms


@dataclass(frozen=True)
class PipelineStats:
    """An immutable snapshot of one run (or one 5-second window)."""

    elapsed_s: float
    grabbed: int
    ticks: int
    sent: int
    dropped_busy: int
    skipped_unsynced: int
    skipped_no_frame: int
    skipped_stale: int
    succeeded: int
    failed: int
    rejected: int
    events_delivered: int
    events_undelivered: int
    max_in_flight: int
    frame_age_p50_ms: float | None
    frame_age_p95_ms: float | None
    rtt_p50_ms: float | None
    rtt_p95_ms: float | None

    @property
    def grab_fps(self) -> float:
        return self.grabbed / self.elapsed_s if self.elapsed_s > 0 else 0.0

    @property
    def send_fps(self) -> float:
        return self.sent / self.elapsed_s if self.elapsed_s > 0 else 0.0


def _ms(value: float | None) -> str:
    return "n/a" if value is None else f"{value:.0f}ms"


def format_stats_line(stats: PipelineStats) -> str:
    """The one-line 5-second summary (F5)."""
    return (
        f"grab {stats.grab_fps:.1f} fps | send {stats.send_fps:.1f} fps | "
        f"dropped_busy {stats.dropped_busy} | "
        f"frame_age p50 {_ms(stats.frame_age_p50_ms)} p95 {_ms(stats.frame_age_p95_ms)} | "
        f"rtt p50 {_ms(stats.rtt_p50_ms)} p95 {_ms(stats.rtt_p95_ms)} | "
        f"in_flight_max {stats.max_in_flight} | events {stats.events_delivered} | "
        f"failed {stats.failed} rejected {stats.rejected} "
        f"unsynced {stats.skipped_unsynced} no_frame {stats.skipped_no_frame} "
        f"stale {stats.skipped_stale}"
    )


class BridgePipeline:
    """One run of the bridge. Construct, ``start`` the grab thread, ``run``."""

    def __init__(
        self,
        config: BridgeConfig,
        grab: GrabThread,
        sender: OneInFlightSender,
        clock_provider: Callable[[], CaptureClock],
        event_sink: EventSink | None = None,
        crop_px: int | None = None,
        stats_hook: Callable[[PipelineStats], None] | None = None,
    ) -> None:
        self._config = config
        self._grab = grab
        self._sender = sender
        self._clock_provider = clock_provider
        self._event_sink = event_sink
        self._crop_px = crop_px if crop_px is not None else config.rung.crop_px
        self._stats_hook = stats_hook
        sender.set_response_handler(self._handle_response)
        self._last_seq = 0
        self._ticks = 0
        self._dropped_busy = 0
        self._skipped_unsynced = 0
        self._skipped_no_frame = 0
        self._skipped_stale = 0
        self._events_delivered = 0
        self._events_undelivered = 0
        self._last_unsynced_log = 0.0
        self._logged_windows = 0

    async def run(self, duration_s: float | None = None) -> PipelineStats:
        """Tick until ``duration_s`` elapses (``None`` = until cancelled)."""
        started = time.monotonic()
        grabbed_at_start = self._grab.frames_grabbed
        next_due = started
        try:
            while duration_s is None or time.monotonic() - started < duration_s:
                await self._tick()
                next_due = await self._sleep_until_next_tick(next_due)
                self._maybe_log_stats(started, grabbed_at_start)
        finally:
            await self._sender.drain()
        return self._snapshot(time.monotonic() - started, self._grab.frames_grabbed - grabbed_at_start)

    async def _sleep_until_next_tick(self, next_due: float) -> float:
        """Fixed-rate pacing that never tries to catch up on a missed tick."""
        period = self._config.tick_period_s
        next_due += period
        delay = next_due - time.monotonic()
        if delay > 0:
            await asyncio.sleep(delay)
            return next_due
        return time.monotonic()

    async def _tick(self) -> None:
        self._ticks += 1
        # Rule 2 first: if the uplink slot is busy this frame is dropped, so
        # there is no point spending 6 remaps and 6 JPEG encodes on it.
        if self._sender.busy:
            self._dropped_busy += 1
            return
        # One hop into a worker thread for the whole cv2 half of the tick:
        # every extra round-trip through the event loop is milliseconds added
        # to frame age at send, which is the number this step is judged on.
        work = await asyncio.to_thread(self._take_and_build)
        if work.skip is not None:
            self._count_skip(work.skip)
            return
        if work.frame_seq:
            self._last_seq = work.frame_seq
        assert work.request is not None  # skip is None, so a request was built
        outcome = self._sender.try_send(work.request, work.t_arrival_laptop_ms)
        if outcome is SendOutcome.DROPPED_BUSY:  # pragma: no cover - pre-checked above
            self._dropped_busy += 1

    def _freshest_frame(self) -> Frame | None:
        """The newest frame, or the next one to arrive if that one is stale."""
        frame = self._grab.wait_for_new(self._last_seq, FRAME_WAIT_TIMEOUT_S)
        if frame is None:
            return None
        if now_ms() - frame.t_arrival_laptop_ms <= MAX_FRAME_AGE_AT_PICKUP_MS:
            return frame
        fresher = self._grab.wait_for_new(frame.seq, FRAME_WAIT_TIMEOUT_S)
        return fresher if fresher is not None else frame

    def _count_skip(self, skip: TickSkip) -> None:
        if skip is TickSkip.NO_FRAME:
            self._skipped_no_frame += 1
        elif skip is TickSkip.STALE_FRAME:
            self._skipped_stale += 1
        elif skip is TickSkip.UNSYNCED:
            self._skipped_unsynced += 1
            self._log_unsynced()

    def _take_and_build(self) -> TickWork:
        """Worker thread: freshest frame -> stamp -> dewarp -> JPEG."""
        frame = self._freshest_frame()
        if frame is None:
            return TickWork(skip=TickSkip.NO_FRAME)
        age_ms = now_ms() - frame.t_arrival_laptop_ms
        if age_ms > MAX_SENDABLE_FRAME_AGE_MS:
            return TickWork(skip=TickSkip.STALE_FRAME, frame_seq=frame.seq)
        try:
            t_capture_ms = self._clock_provider().capture_time_ms(frame.t_arrival_laptop_ms)
        except ClockNotSyncedError:
            return TickWork(skip=TickSkip.UNSYNCED, frame_seq=frame.seq)
        request = self._build_request(frame, t_capture_ms)
        if request is None:
            return TickWork(skip=TickSkip.ENCODE_FAILED, frame_seq=frame.seq)
        return TickWork(
            skip=None,
            request=request,
            frame_seq=frame.seq,
            t_arrival_laptop_ms=frame.t_arrival_laptop_ms,
        )

    def _log_unsynced(self) -> None:
        """At most one line per 5 s: this fires on every tick when it fires."""
        now = time.monotonic()
        if now - self._last_unsynced_log < UNSYNCED_LOG_INTERVAL_S:
            return
        self._last_unsynced_log = now
        logger.warning(
            "phone clock not synced yet; skipping frames (%d so far). "
            "Connect the phone, or run with --allow-unsynced.",
            self._skipped_unsynced,
        )

    def _build_request(self, frame: Frame, t_capture_ms: int) -> InferenceRequest | None:
        """Dewarp + encode. Runs in a worker thread; never raises into the loop."""
        try:
            cropset = dewarp_equirect(frame.image, self._crop_px, frame.t_arrival_laptop_ms)
            crops = encode_cropset(cropset, self._config.rung.jpeg_quality)
        except (ValueError, RuntimeError) as exc:
            logger.warning("dropping frame %d: %s", frame.seq, exc)
            return None
        meta = InferenceMeta(
            schema=1,
            t_capture_ms=t_capture_ms,
            rung=self._config.rung.index,
            crop_px=self._crop_px,
            yaws_deg=CROP_YAWS_DEG,
            session_id=self._config.session_id,
        )
        return InferenceRequest(meta=meta, crops=crops)

    async def _handle_response(self, response: InferenceResponse) -> None:
        events = events_from_response(response, CROP_YAWS_DEG, self._config.delta_deg)
        if self._event_sink is None:
            self._events_undelivered += len(events)
            return
        for event in events:
            if await self._event_sink.send_event(event):
                self._events_delivered += 1
            else:
                self._events_undelivered += 1

    def _maybe_log_stats(self, started: float, grabbed_at_start: int) -> None:
        elapsed = time.monotonic() - started
        window = int(elapsed / self._config.stats_interval_s)
        if window <= self._logged_windows:
            return
        self._logged_windows = window
        stats = self._snapshot(elapsed, self._grab.frames_grabbed - grabbed_at_start)
        logger.info("%s", format_stats_line(stats))
        if self._stats_hook is not None:
            self._stats_hook(stats)

    def _snapshot(self, elapsed_s: float, grabbed: int) -> PipelineStats:
        sender = self._sender.stats
        return PipelineStats(
            elapsed_s=elapsed_s,
            grabbed=grabbed,
            ticks=self._ticks,
            sent=sender.sent,
            # The pipeline pre-checks the slot, so the sender's own counter is
            # only a safety net; adding them keeps one honest total.
            dropped_busy=self._dropped_busy + sender.dropped_busy,
            skipped_unsynced=self._skipped_unsynced,
            skipped_no_frame=self._skipped_no_frame,
            skipped_stale=self._skipped_stale,
            succeeded=sender.succeeded,
            failed=sender.failed,
            rejected=sender.rejected,
            events_delivered=self._events_delivered,
            events_undelivered=self._events_undelivered,
            max_in_flight=sender.max_in_flight,
            frame_age_p50_ms=sender.frame_age_p50_ms,
            frame_age_p95_ms=sender.frame_age_p95_ms,
            rtt_p50_ms=sender.rtt_p50_ms,
            rtt_p95_ms=sender.rtt_p95_ms,
        )


def now_laptop_ms() -> int:
    """Re-exported so callers need only one import for "the laptop clock"."""
    return now_ms()
