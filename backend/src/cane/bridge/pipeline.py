"""The bridge loop: grab -> dewarp -> encode -> send -> events -> phone.

Paced at the *current* rung's fps, re-read every tick: step 8's health
monitor may step a rung or flip to the local detector between any two
frames, and the size, quality and rate on the wire follow it without
anything being rebuilt (E3).

Every tick takes the *newest* frame (rule 1) and
sends only if the uplink slot is free (rule 2); both of those failing silently
is what design Sec 3.3 warns about, so the 5-second stats line prints the
numbers that would expose it: grab fps, send fps, dropped_busy, frame age at
send p50/p95, request RTT p50/p95, and the frames dropped for missing the
frame deadline.

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
from cane.bridge.deadline import FRAME_DEADLINE_MS, past_deadline
from cane.bridge.dewarp import CROP_YAWS_DEG, CropSet, encode_cropset, dewarp_equirect
from cane.bridge.events import events_from_response
from cane.bridge.health import HealthMonitor
from cane.bridge.log_throttle import LogThrottle
from cane.bridge.sender import OneInFlightSender, SendOutcome
# Re-exported for callers that imported them from here before step 8 split
# the stats line out of this module.
from cane.bridge.stats import PipelineStats, format_stats_line, health_snapshot
from cane.bridge.uplink import RungPlan, RungPlanner
from cane.cloud.api_models import (
    SCHEMA_VERSION,
    InferenceMeta,
    InferenceRequest,
    InferenceResponse,
)
from cane.contracts.awareness_event import AwarenessEvent, Source
from cane.transport.clock_sync import ClockNotSyncedError
from cane.transport.wall_clock import now_ms

logger = logging.getLogger(__name__)

FRAME_WAIT_TIMEOUT_S = 0.25
# The freshness retry below is looking for the *next* grab, which at 30 fps
# is at most 33 ms away; one grab period plus slack is the whole budget.
# Reusing FRAME_WAIT_TIMEOUT_S here would stall a tick for 250 ms chasing a
# frame that is not coming from a stalled camera.
FRESHNESS_RETRY_TIMEOUT_S = 0.04
# One line per reason per interval: a skip that repeats every tick must not
# drown the 5-second stats line that carries the actual counts.
SKIP_LOG_INTERVAL_S = 5.0
# A busy slot for one tick is the one-in-flight rule working as designed. A
# busy slot for four ticks in a row is a backend that has stopped answering
# while still holding the slot, and every frame in between is dropped with
# nothing in the log naming the culprit. Counting alone is not enough here:
# `dropped_busy` looks identical whether the uplink is merely slow or one
# request has wedged.
BUSY_WATCHDOG_TICKS = 3
# A 30 fps camera means the frame sitting in the slot is on average half a
# frame period (17 ms) old, and up to 33 ms old, before we have done any work.
# When the one already there is staler than this, wait for the next arrival
# instead: the send happens a few ms later but carries pixels from a later
# instant of reality, which is strictly better for a warning system -- and it
# is what keeps frame age at send well inside the 50 ms budget.
MAX_FRAME_AGE_AT_PICKUP_MS = 10
# The ceiling on the whole tick is the frame's own age: see
# ``deadline.FRAME_DEADLINE_MS``. It is re-checked at every stage boundary
# below, so a slow dewarp or a stalled camera both end the same way -- the
# frame is dropped and counted, never shipped late.


class TickSkip(StrEnum):
    """Why a tick produced no request. Counted on the loop thread."""

    NO_FRAME = "no_frame"
    DEADLINE_EXCEEDED = "deadline_exceeded"
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
        monitor: HealthMonitor | None = None,
        source_provider: Callable[[], Source] | None = None,
    ) -> None:
        self._config = config
        self._grab = grab
        self._sender = sender
        self._clock_provider = clock_provider
        self._event_sink = event_sink
        self._monitor = monitor
        self._planner = RungPlanner(config, monitor, crop_px)
        # Which backend answered the response now being handled. Constant
        # for a run with no monitor, which is every pre-step-8 caller.
        self._source_provider = source_provider
        self._stats_hook = stats_hook
        sender.set_response_handler(self._handle_response)
        self._last_seq = 0
        self._ticks = 0
        self._dropped_busy = 0
        self._skipped_unsynced = 0
        self._skipped_no_frame = 0
        self._skipped_deadline = 0
        self._events_delivered = 0
        self._events_undelivered = 0
        self._unsynced_log = LogThrottle(SKIP_LOG_INTERVAL_S)
        self._deadline_log = LogThrottle(SKIP_LOG_INTERVAL_S)
        self._busy_log = LogThrottle(SKIP_LOG_INTERVAL_S)
        self._busy_ticks = 0
        self._logged_windows = 0

    async def run(self, duration_s: float | None = None) -> PipelineStats:
        """Tick until ``duration_s`` elapses (``None`` = until cancelled)."""
        started = time.monotonic()
        grabbed_at_start = self._grab.frames_grabbed
        next_due = started
        try:
            while duration_s is None or time.monotonic() - started < duration_s:
                plan = self._planner.current()
                await self._tick(plan)
                next_due = await self._sleep_until_next_tick(next_due, plan.period_s)
                self._maybe_log_stats(started, grabbed_at_start)
        finally:
            await self._sender.drain()
        return self._snapshot(time.monotonic() - started, self._grab.frames_grabbed - grabbed_at_start)

    async def _sleep_until_next_tick(self, next_due: float, period_s: float) -> float:
        """Fixed-rate pacing that never tries to catch up on a missed tick.

        The period is passed in rather than read from the config: a rung
        step changes the send rate, and the very next sleep must use it.
        """
        next_due += period_s
        delay = next_due - time.monotonic()
        if delay > 0:
            await asyncio.sleep(delay)
            return next_due
        # Overrun: there is no time left to sleep, but the loop must still
        # yield -- the request task this tick just created has not run yet,
        # and returning synchronously would hold it back until the next
        # await, adding a whole tick to frame age at send.
        await asyncio.sleep(0)
        return time.monotonic()

    async def _tick(self, plan: RungPlan) -> None:
        self._ticks += 1
        # Rule 2 first: if the uplink slot is busy this frame is dropped, so
        # there is no point spending 6 remaps and 6 JPEG encodes on it.
        if self._sender.busy:
            self._dropped_busy += 1
            self._busy_ticks += 1
            if self._busy_ticks > BUSY_WATCHDOG_TICKS:
                self._log_busy()
            return
        self._busy_ticks = 0
        # One hop into a worker thread for the whole cv2 half of the tick:
        # every extra round-trip through the event loop is milliseconds added
        # to frame age at send, which is the number this step is judged on.
        work = await asyncio.to_thread(self._take_and_build, plan)
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
        fresher = self._grab.wait_for_new(frame.seq, FRESHNESS_RETRY_TIMEOUT_S)
        return fresher if fresher is not None else frame

    def _count_skip(self, skip: TickSkip) -> None:
        if skip is TickSkip.NO_FRAME:
            self._skipped_no_frame += 1
        elif skip is TickSkip.DEADLINE_EXCEEDED:
            self._skipped_deadline += 1
            self._log_deadline()
        elif skip is TickSkip.UNSYNCED:
            self._skipped_unsynced += 1
            self._log_unsynced()

    def _take_and_build(self, plan: RungPlan) -> TickWork:
        """Worker thread: freshest frame -> stamp -> dewarp -> JPEG.

        The deadline is re-checked after every stage, because those stages
        are the only places in a tick where time passes. Each check is one
        integer compare, so the ceiling costs nothing to enforce.
        """
        frame = self._freshest_frame()
        if frame is None:
            return TickWork(skip=TickSkip.NO_FRAME)
        if (expired := self._expired(frame)) is not None:
            return expired
        try:
            t_capture_ms = self._clock_provider().capture_time_ms(frame.t_arrival_laptop_ms)
        except ClockNotSyncedError:
            return TickWork(skip=TickSkip.UNSYNCED, frame_seq=frame.seq)
        cropset = self._dewarp(frame, plan.crop_px)
        if cropset is None:
            return TickWork(skip=TickSkip.ENCODE_FAILED, frame_seq=frame.seq)
        if (expired := self._expired(frame)) is not None:
            return expired
        crops = self._encode(cropset, frame.seq, plan.jpeg_quality)
        if crops is None:
            return TickWork(skip=TickSkip.ENCODE_FAILED, frame_seq=frame.seq)
        if (expired := self._expired(frame)) is not None:
            return expired
        return TickWork(
            skip=None,
            request=InferenceRequest(meta=self._meta(t_capture_ms, plan), crops=crops),
            frame_seq=frame.seq,
            t_arrival_laptop_ms=frame.t_arrival_laptop_ms,
        )

    @staticmethod
    def _expired(frame: Frame) -> TickWork | None:
        """The one stage-boundary check: too old to be worth any more work."""
        if not past_deadline(frame.t_arrival_laptop_ms):
            return None
        return TickWork(skip=TickSkip.DEADLINE_EXCEEDED, frame_seq=frame.seq)

    def _log_unsynced(self) -> None:
        """At most one line per interval: this fires on every tick when it fires."""
        if not self._unsynced_log.allow():
            return
        logger.warning(
            "phone clock not synced yet; skipping frames (%d so far). "
            "Connect the phone, or run with --allow-unsynced.",
            self._skipped_unsynced,
        )

    def _log_busy(self) -> None:
        """Which backend is holding the slot, at most once per interval."""
        if not self._busy_log.allow():
            return
        logger.warning(
            "the uplink slot has been busy for %d consecutive ticks (backend %s); "
            "%d frame(s) dropped so far -- the request in flight is not returning.",
            self._busy_ticks,
            self._backend_in_flight(),
            self._dropped_busy,
        )

    def _backend_in_flight(self) -> str:
        """The monitor's current mode names the backend the pending request
        went to: the sender holds one request at a time, and a mode change
        is decided by that same request's own outcome.
        """
        return "cloud" if self._monitor is None else str(self._monitor.mode)

    def _log_deadline(self) -> None:
        """Same cadence, same reason: a per-tick drop must not spam the log."""
        if not self._deadline_log.allow():
            return
        logger.warning(
            "dropped %d frame(s) older than the %d ms deadline; the camera has "
            "stalled or the laptop is not keeping up with dewarp and encode.",
            self._skipped_deadline,
            FRAME_DEADLINE_MS,
        )

    def _dewarp(self, frame: Frame, crop_px: int) -> CropSet | None:
        """Six perspective crops. Worker thread; never raises into the loop."""
        try:
            return dewarp_equirect(frame.image, crop_px, frame.t_arrival_laptop_ms)
        except (ValueError, RuntimeError) as exc:
            logger.warning("dropping frame %d in dewarp: %s", frame.seq, exc)
            return None

    def _encode(
        self, cropset: CropSet, frame_seq: int, jpeg_quality: int
    ) -> tuple[bytes, ...] | None:
        """JPEG at the rung's quality. Worker thread; never raises."""
        try:
            return encode_cropset(cropset, jpeg_quality)
        except (ValueError, RuntimeError) as exc:
            logger.warning("dropping frame %d in encode: %s", frame_seq, exc)
            return None

    def _meta(self, t_capture_ms: int, plan: RungPlan) -> InferenceMeta:
        """The rung and crop size are the ones this frame actually used --
        never the ones the run started with (E3).
        """
        return InferenceMeta(
            schema=SCHEMA_VERSION,
            t_capture_ms=t_capture_ms,
            rung=plan.index,
            crop_px=plan.crop_px,
            yaws_deg=CROP_YAWS_DEG,
            session_id=self._config.session_id,
        )

    async def _handle_response(self, response: InferenceResponse) -> None:
        events = events_from_response(
            response, CROP_YAWS_DEG, self._config.delta_deg, self._source()
        )
        if self._event_sink is None:
            self._events_undelivered += len(events)
            return
        for event in events:
            if await self._event_sink.send_event(event):
                self._events_delivered += 1
            else:
                self._events_undelivered += 1

    def _source(self) -> Source:
        """Who answered *this* response. The sender holds one request at a
        time and calls this handler in the same task, so the provider is
        never reporting some other frame's backend.
        """
        return Source.X4_CLOUD if self._source_provider is None else self._source_provider()

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
            # Same shape as dropped_busy: the pipeline drops what it can
            # see coming, the sender catches what expired after hand-off.
            skipped_deadline=self._skipped_deadline + sender.dropped_deadline,
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
            health=health_snapshot(self._monitor),
        )

