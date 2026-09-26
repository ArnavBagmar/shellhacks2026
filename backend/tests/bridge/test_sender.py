"""One request in flight, EVER (F2 rule 2) -- the step's acceptance criterion.

The uplink is deliberately slower than the send tick in both timing tests;
what must stay true is that frame age at send never grows, because frames are
dropped rather than queued.
"""

from __future__ import annotations

import asyncio
import json
import time

import pytest

from cane.bridge.deadline import FRAME_DEADLINE_MS
from cane.bridge.metrics import RollingWindow, percentile
from cane.bridge.sender import STATS_WINDOW, OneInFlightSender, SendOutcome
from cane.cloud.api_models import CROP_COUNT, InferenceMeta, InferenceRequest, InferenceResponse
from cane.transport.wall_clock import now_ms

TICK_S = 0.1
RUN_TICKS = 20
SLOW_TRANSPORT_S = 0.3
MAX_FRAME_AGE_MS = 50
# A synchronous block of the event loop between try_send() and the next
# await: exactly what CPU load does to the real pipeline.
LOOP_BLOCK_S = 0.03
LOOP_BLOCK_MS = 30
SAMPLING_SLACK_MS = 8
# Old enough that the 30 ms block below pushes it past the deadline.
NEARLY_EXPIRED_AGE_MS = FRAME_DEADLINE_MS - LOOP_BLOCK_MS + 10
YAWS = (0.0, 60.0, 120.0, 180.0, -120.0, -60.0)
T_CAPTURE_MS = 1_790_000_000_000


class FakeTransport:
    """Records every request and answers after a configurable delay."""

    def __init__(self, delays_s: tuple[float, ...] = (SLOW_TRANSPORT_S,)) -> None:
        self._delays_s = delays_s
        self.requests: list[InferenceRequest] = []
        self.in_flight = 0
        self.max_in_flight = 0
        self.closed = False

    async def post_crops(self, request: InferenceRequest) -> bytes:
        self.in_flight += 1
        self.max_in_flight = max(self.max_in_flight, self.in_flight)
        try:
            await asyncio.sleep(self._delays_s[len(self.requests) % len(self._delays_s)])
            self.requests.append(request)
        finally:
            self.in_flight -= 1
        return json.dumps(
            {
                "schema": 1,
                "t_capture_ms": request.meta.t_capture_ms,
                "inference_ms": 1.0,
                "detections": [],
            }
        ).encode()

    async def aclose(self) -> None:
        self.closed = True


class ClockRecordingTransport:
    """Records the wall clock at the instant the request reaches the wire."""

    def __init__(self) -> None:
        self.entered_at_ms: list[int] = []

    async def post_crops(self, request: InferenceRequest) -> bytes:
        self.entered_at_ms.append(now_ms())
        return json.dumps(
            {
                "schema": 1,
                "t_capture_ms": request.meta.t_capture_ms,
                "inference_ms": 1.0,
                "detections": [],
            }
        ).encode()

    async def aclose(self) -> None:
        return None


class RaisingTransport:
    def __init__(self) -> None:
        self.calls = 0

    async def post_crops(self, request: InferenceRequest) -> bytes:
        self.calls += 1
        raise ConnectionError("stub unreachable")

    async def aclose(self) -> None:
        return None


class GarbageTransport:
    async def post_crops(self, request: InferenceRequest) -> bytes:
        return b'{"schema": 1, "t_capture_ms": "not-an-int"}'

    async def aclose(self) -> None:
        return None


def _request(t_capture_ms: int = T_CAPTURE_MS) -> InferenceRequest:
    meta = InferenceMeta(
        schema=1,
        t_capture_ms=t_capture_ms,
        rung=1,
        crop_px=640,
        yaws_deg=YAWS,
        session_id="test",
    )
    return InferenceRequest(meta=meta, crops=tuple(b"jpeg" for _ in range(CROP_COUNT)))


async def test_slow_uplink_drops_frames_and_keeps_frame_age_under_50ms() -> None:
    transport = FakeTransport()
    sender = OneInFlightSender(transport)
    outcomes: list[SendOutcome] = []

    for _ in range(RUN_TICKS):
        outcomes.append(sender.try_send(_request(), t_arrival_laptop_ms=now_ms()))
        await asyncio.sleep(TICK_S)
    await sender.aclose()

    sent = outcomes.count(SendOutcome.SENT)
    assert 5 <= sent <= 8, f"expected ~2.0s/0.3s sends, got {sent}"
    assert outcomes.count(SendOutcome.DROPPED_BUSY) == RUN_TICKS - sent
    stats = sender.stats
    assert stats.sent == sent
    assert stats.dropped_busy == RUN_TICKS - sent
    assert stats.frame_age_p95_ms is not None
    assert stats.frame_age_p95_ms < MAX_FRAME_AGE_MS
    assert transport.max_in_flight == 1
    assert stats.max_in_flight == 1


async def test_latency_stays_bounded_as_the_transport_slows_to_two_seconds() -> None:
    transport = FakeTransport(delays_s=(0.1, 0.5, 1.0, 2.0))
    sender = OneInFlightSender(transport)
    ages_observed: list[int] = []

    for _ in range(30):
        arrival = now_ms()
        if sender.try_send(_request(), t_arrival_laptop_ms=arrival) is SendOutcome.SENT:
            ages_observed.append(now_ms() - arrival)
        assert sender.in_flight <= 1
        await asyncio.sleep(TICK_S)
    await sender.aclose()

    stats = sender.stats
    assert transport.max_in_flight == 1
    assert stats.max_in_flight == 1
    assert max(ages_observed) < MAX_FRAME_AGE_MS
    assert stats.frame_age_p95_ms is not None and stats.frame_age_p95_ms < MAX_FRAME_AGE_MS
    assert stats.rtt_p95_ms is not None and stats.rtt_p95_ms >= 100


async def test_frame_age_is_measured_not_assumed() -> None:
    transport = FakeTransport(delays_s=(0.0,))
    sender = OneInFlightSender(transport)

    sender.try_send(_request(), t_arrival_laptop_ms=now_ms() - 20)
    await sender.aclose()

    stats = sender.stats
    assert stats.frame_age_p50_ms is not None
    assert 15 <= stats.frame_age_p50_ms <= 40


async def test_frame_age_is_sampled_at_the_wire_not_at_try_send() -> None:
    """The number this step is judged on must be the true time-to-wire.

    ``try_send`` only *creates* the request task; if the loop is busy, the
    body is built and posted a whole tick later. Sampling the clock in
    ``try_send`` would under-count that gap and the reported p95 would be
    fiction.
    """
    transport = ClockRecordingTransport()
    sender = OneInFlightSender(transport)

    arrival_ms = now_ms()
    assert sender.try_send(_request(), t_arrival_laptop_ms=arrival_ms) is SendOutcome.SENT
    time.sleep(LOOP_BLOCK_S)
    await sender.aclose()

    assert len(transport.entered_at_ms) == 1
    age_at_wire_ms = transport.entered_at_ms[0] - arrival_ms
    assert age_at_wire_ms >= LOOP_BLOCK_MS
    recorded = sender.stats.frame_age_p50_ms
    assert recorded is not None
    assert recorded >= LOOP_BLOCK_MS, f"reported {recorded} ms, true age {age_at_wire_ms} ms"
    assert abs(recorded - age_at_wire_ms) <= SAMPLING_SLACK_MS


async def test_a_frame_that_ages_past_the_deadline_before_the_wire_is_dropped() -> None:
    """The last gate is inside the request task, not at try_send.

    try_send only creates the task; if the loop then stalls, the frame can be
    over the deadline by the time the body runs. Posting it anyway would ship
    a warning the phone would treat as current.
    """
    transport = ClockRecordingTransport()
    sender = OneInFlightSender(transport)

    arrival_ms = now_ms() - NEARLY_EXPIRED_AGE_MS
    assert sender.try_send(_request(), t_arrival_laptop_ms=arrival_ms) is SendOutcome.SENT
    time.sleep(LOOP_BLOCK_S)
    await sender.aclose()

    assert transport.entered_at_ms == []
    stats = sender.stats
    assert stats.dropped_deadline == 1
    assert stats.sent == 0
    # A frame that never reached the wire must not pollute the age at send.
    assert stats.frame_age_p50_ms is None


async def test_a_frame_already_past_the_deadline_is_never_started() -> None:
    transport = ClockRecordingTransport()
    sender = OneInFlightSender(transport)

    outcome = sender.try_send(
        _request(), t_arrival_laptop_ms=now_ms() - FRAME_DEADLINE_MS - 50
    )
    await sender.aclose()

    assert outcome is SendOutcome.DROPPED_DEADLINE
    assert transport.entered_at_ms == []
    assert sender.stats.dropped_deadline == 1
    assert sender.in_flight == 0


async def test_the_response_handler_receives_the_parsed_model() -> None:
    transport = FakeTransport(delays_s=(0.0,))
    seen: list[InferenceResponse] = []

    async def handler(response: InferenceResponse) -> None:
        seen.append(response)

    sender = OneInFlightSender(transport, on_response=handler)
    assert sender.try_send(_request(), t_arrival_laptop_ms=now_ms()) is SendOutcome.SENT
    await sender.aclose()

    assert len(seen) == 1
    assert seen[0].t_capture_ms == T_CAPTURE_MS
    assert sender.stats.succeeded == 1


async def test_a_transport_error_frees_the_slot_and_is_counted() -> None:
    transport = RaisingTransport()
    sender = OneInFlightSender(transport)

    assert sender.try_send(_request(), t_arrival_laptop_ms=now_ms()) is SendOutcome.SENT
    await sender.drain()
    assert sender.in_flight == 0
    assert sender.try_send(_request(), t_arrival_laptop_ms=now_ms()) is SendOutcome.SENT
    await sender.aclose()

    assert transport.calls == 2
    assert sender.stats.failed == 2
    assert sender.stats.succeeded == 0


async def test_an_invalid_cloud_response_is_rejected_not_crashed() -> None:
    called: list[InferenceResponse] = []

    async def handler(response: InferenceResponse) -> None:  # pragma: no cover - must not run
        called.append(response)

    sender = OneInFlightSender(GarbageTransport(), on_response=handler)
    sender.try_send(_request(), t_arrival_laptop_ms=now_ms())
    await sender.aclose()

    assert called == []
    assert sender.stats.rejected == 1
    assert sender.stats.succeeded == 0


async def test_closing_awaits_the_pending_request_and_closes_the_transport() -> None:
    transport = FakeTransport(delays_s=(0.05,))
    sender = OneInFlightSender(transport)

    sender.try_send(_request(), t_arrival_laptop_ms=now_ms())
    await sender.aclose()

    assert sender.in_flight == 0
    assert transport.closed is True
    assert len(transport.requests) == 1


def test_percentiles_of_a_known_sample() -> None:
    values = [float(v) for v in range(1, 101)]
    assert percentile(values, 50) == pytest.approx(50.5, abs=1.0)
    assert percentile(values, 95) == pytest.approx(95.0, abs=1.0)
    assert percentile([], 50) is None
    assert percentile([7.0], 95) == 7.0


def test_the_rolling_window_is_bounded() -> None:
    window = RollingWindow(maxlen=STATS_WINDOW)
    for value in range(STATS_WINDOW + 25):
        window.add(float(value))

    assert len(window) == STATS_WINDOW
    assert window.p50 is not None and window.p50 > STATS_WINDOW / 2
