"""Asking the cloud whether it is back, without getting in the way.

Three constraints shape this module (step-07-design H1, task-8-brief E2.5):

* it polls `GET /healthz` at 1 Hz and **never** `/warmup`, which takes the
  inference lock and would stall the service it is asking about;
* it runs only while the monitor is LOCAL -- there is no reason to add
  requests to an uplink that is already working;
* it lives outside the one-in-flight sender, on its own client, so a probe
  can never occupy the slot a frame needs.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import Awaitable, Callable, Final, Mapping, Protocol

import httpx

from cane.bridge.backends import DEFAULT_BACKEND_TIMEOUT_S
from cane.bridge.health import PROBE_INTERVAL_S, HealthMonitor
from cane.bridge.log_throttle import LogThrottle
from cane.transport.wall_clock import now_ms

logger = logging.getLogger(__name__)

HEALTHZ_PATH: Final = "/healthz"
INFER_PATH: Final = "/infer"
HTTP_OK: Final = 200
#: Only the TensorRT path has an engine that can be cold; every other
#: detector is ready the moment the process answers (E2 rule 5).
ENGINE_DETECTOR: Final = "tensorrt"
DETECTOR_KEY: Final = "detector"
ENGINE_READY_KEY: Final = "engine_ready"

Clock = Callable[[], int]
Sleep = Callable[[float], Awaitable[None]]


@dataclass(frozen=True, slots=True)
class HealthzReading:
    """One answer from `GET /healthz`."""

    status: int
    payload: Mapping[str, object]


class HealthzSource(Protocol):
    """Whatever actually asks. Tests pass a fake; the bridge passes HTTP."""

    async def fetch(self) -> HealthzReading: ...

    async def aclose(self) -> None: ...


def healthz_url_for(inference_url: str) -> str:
    """`http://host:8000/infer` -> `http://host:8000/healthz`."""
    base = inference_url
    if base.endswith(INFER_PATH):
        base = base[: -len(INFER_PATH)]
    return f"{base.rstrip('/')}{HEALTHZ_PATH}"


def is_healthy_reading(reading: HealthzReading) -> bool:
    """200 plus a detector that can actually serve the next frame."""
    if reading.status != HTTP_OK:
        return False
    if reading.payload.get(DETECTOR_KEY) != ENGINE_DETECTOR:
        return True
    return reading.payload.get(ENGINE_READY_KEY) is True


class HttpHealthz:
    """The real probe: its own short-lived client, never the sender's."""

    def __init__(self, url: str, timeout_s: float = DEFAULT_BACKEND_TIMEOUT_S) -> None:
        self._url = url
        self._client = httpx.AsyncClient(timeout=httpx.Timeout(timeout_s))

    @property
    def url(self) -> str:
        return self._url

    async def fetch(self) -> HealthzReading:
        response = await self._client.get(self._url)
        payload: Mapping[str, object] = {}
        if response.status_code == HTTP_OK:
            try:
                decoded = response.json()
            except ValueError:
                decoded = None
            if isinstance(decoded, dict):
                payload = decoded
        return HealthzReading(status=response.status_code, payload=payload)

    async def aclose(self) -> None:
        await self._client.aclose()


class CloudProbe:
    """`await probe.run()` as a background task for the life of the bridge."""

    def __init__(
        self,
        source: HealthzSource,
        monitor: HealthMonitor,
        clock: Clock = now_ms,
        interval_s: float = PROBE_INTERVAL_S,
        sleep: Sleep = asyncio.sleep,
    ) -> None:
        if interval_s <= 0:
            raise ValueError(f"interval_s must be > 0, got {interval_s}")
        self._source = source
        self._monitor = monitor
        self._clock = clock
        self._interval_s = interval_s
        self._sleep = sleep
        self._failure_log = LogThrottle()

    async def probe_once(self) -> bool:
        """One `/healthz`, folded into the monitor. Returns whether the
        endpoint answered at all -- the monitor decides what "healthy"
        means, because the RTT ceiling is its constant, not ours.
        """
        if not self._monitor.is_local:
            return False
        started_ms = self._clock()
        answered, rtt_ms = await self._read(started_ms)
        self._monitor.observe_probe(answered, rtt_ms, self._clock())
        return answered

    async def _read(self, started_ms: int) -> tuple[bool, float | None]:
        try:
            reading = await self._source.fetch()
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - a dead cloud is the normal case here
            self._log_failure(exc)
            return False, None
        rtt_ms = float(self._clock() - started_ms)
        healthy = is_healthy_reading(reading)
        logger.debug(
            "healthz %d in %.0f ms -> %s", reading.status, rtt_ms, "up" if healthy else "down"
        )
        return healthy, rtt_ms

    def _log_failure(self, exc: BaseException) -> None:
        """Throttled: while the cloud is down this fires every second, and
        the counts are already on the 5-second stats line.
        """
        if not self._failure_log.allow():
            return
        logger.info("healthz probe failed (%s: %s); staying local", type(exc).__name__, exc)

    async def run(self) -> None:
        """Sleep first: the flip that started us happened a moment ago."""
        while True:
            await self._sleep(self._interval_s)
            try:
                await self.probe_once()
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001 - the prober must outlive any fault
                logger.exception("health probe raised; continuing")

    async def aclose(self) -> None:
        await self._source.aclose()
