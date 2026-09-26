"""The 1 Hz `/healthz` prober that runs only while local (E2 rule 5).

Never `/warmup`: that endpoint takes the inference lock, so using it as a
health check would stall the very service it is asking about
(step-07-design H1).
"""

from __future__ import annotations

import asyncio
import logging

import httpx
import pytest

from cane.bridge.health import (
    RECOVER_HEALTHY_S,
    STEP_DOWN_P95_MS,
    HealthMonitor,
    Mode,
    Timeout,
)
from cane.bridge.probe import (
    PROBE_INTERVAL_S,
    CloudProbe,
    HealthzReading,
    healthz_url_for,
    is_healthy_reading,
)

T0 = 1_790_000_000_000
S = 1000
FAST_MS = 30
HTTP_OK = 200
HTTP_UNAVAILABLE = 503


class FakeClock:
    """Integer milliseconds, advanced only by the test (or by a probe)."""

    def __init__(self, now_ms: int = T0, cost_ms: int = FAST_MS) -> None:
        self.now_ms = now_ms
        self.cost_ms = cost_ms

    def __call__(self) -> int:
        return self.now_ms

    def advance(self, ms: int) -> None:
        self.now_ms += ms


class FakeHealthz:
    """Answers with a scripted reading, or raises."""

    def __init__(self, reading: object, clock: FakeClock) -> None:
        self.reading = reading
        self.calls = 0
        self.closed = False
        self._clock = clock

    async def fetch(self) -> HealthzReading:
        self.calls += 1
        self._clock.advance(self._clock.cost_ms)
        if isinstance(self.reading, BaseException):
            raise self.reading
        assert isinstance(self.reading, HealthzReading)
        return self.reading

    async def aclose(self) -> None:
        self.closed = True


def _ready() -> HealthzReading:
    return HealthzReading(
        status=HTTP_OK,
        payload={"detector": "tensorrt", "engine_ready": True, "last_inference_ms": 21.0},
    )


def _local_monitor() -> HealthMonitor:
    monitor = HealthMonitor(Mode.cloud(1))
    for index in range(3):
        monitor.observe(Timeout(), T0 + index)
    assert monitor.is_local
    return monitor


def test_the_healthz_url_is_derived_from_the_inference_url() -> None:
    assert healthz_url_for("http://127.0.0.1:8000/infer") == "http://127.0.0.1:8000/healthz"
    assert healthz_url_for("https://cane.example/v1/infer") == "https://cane.example/v1/healthz"
    assert healthz_url_for("http://host:8000/") == "http://host:8000/healthz"


def test_a_ready_engine_is_healthy_and_a_cold_one_is_not() -> None:
    assert is_healthy_reading(_ready())
    cold = HealthzReading(status=HTTP_OK, payload={"detector": "tensorrt", "engine_ready": False})
    assert not is_healthy_reading(cold)
    # Outside TensorRT there is no engine to be ready: a 200 is enough.
    warm_fake = HealthzReading(status=HTTP_OK, payload={"detector": "fake", "engine_ready": False})
    assert is_healthy_reading(warm_fake)
    assert not is_healthy_reading(HealthzReading(status=HTTP_UNAVAILABLE, payload={}))


async def test_a_probe_feeds_the_monitor_and_thirty_seconds_recovers() -> None:
    clock = FakeClock()
    monitor = _local_monitor()
    probe = CloudProbe(FakeHealthz(_ready(), clock), monitor, clock=clock)

    for _ in range(RECOVER_HEALTHY_S + 1):
        await probe.probe_once()
        clock.advance(S - FAST_MS)

    # The flip happened on rung 1, so the cloud is re-entered on rung 2.
    assert monitor.state.mode == Mode.cloud(2)
    assert monitor.state.counters.recoveries == 1


async def test_a_probe_that_raises_is_counted_not_propagated(
    caplog: pytest.LogCaptureFixture,
) -> None:
    clock = FakeClock()
    monitor = _local_monitor()
    source = FakeHealthz(httpx.ConnectError("connection refused"), clock)
    probe = CloudProbe(source, monitor, clock=clock)

    with caplog.at_level(logging.DEBUG, logger="cane.bridge.probe"):
        healthy = await probe.probe_once()

    assert healthy is False
    assert monitor.state.counters.probes_unhealthy == 1
    assert monitor.is_local


async def test_a_slow_probe_is_not_a_healthy_probe() -> None:
    clock = FakeClock(cost_ms=STEP_DOWN_P95_MS + 1)
    monitor = _local_monitor()
    probe = CloudProbe(FakeHealthz(_ready(), clock), monitor, clock=clock)

    assert await probe.probe_once() is True  # the endpoint answered...
    assert monitor.state.counters.probes_unhealthy == 1  # ...but far too slowly


async def test_no_probe_is_issued_while_the_cloud_is_in_use() -> None:
    clock = FakeClock()
    monitor = HealthMonitor(Mode.cloud(2))
    source = FakeHealthz(_ready(), clock)
    probe = CloudProbe(source, monitor, clock=clock)

    assert await probe.probe_once() is False
    assert source.calls == 0
    assert monitor.state.counters == monitor.state.counters.plus()


async def test_the_run_loop_probes_once_per_interval_and_cancels_cleanly() -> None:
    clock = FakeClock()
    monitor = _local_monitor()
    source = FakeHealthz(_ready(), clock)
    slept: list[float] = []

    async def fake_sleep(seconds: float) -> None:
        slept.append(seconds)
        clock.advance(int(seconds * S))
        await asyncio.sleep(0)

    probe = CloudProbe(source, monitor, clock=clock, sleep=fake_sleep)
    task = asyncio.create_task(probe.run())
    for _ in range(200):
        if source.calls >= 3:
            break
        await asyncio.sleep(0)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert source.calls >= 3
    assert slept[:3] == [PROBE_INTERVAL_S] * 3
    await probe.aclose()
    assert source.closed
