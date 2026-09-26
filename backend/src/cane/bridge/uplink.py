"""What joins the sender, the backends and the health monitor (E1, E3).

Two small pieces, both deliberately kept out of `pipeline.py`:

`MonitoredUplink` wears the step-6 `InferenceTransport` face so the
`OneInFlightSender` keeps its one-in-flight rule *unchanged* -- local
inference also takes time and must never queue either -- while timing every
call and folding the outcome into the monitor. It also remembers which
backend answered, because the event the phone receives must say `x4_cloud`
or `x4_local` according to who actually produced it, not according to
whatever the mode became while the request was in the air. That read is safe
for exactly one reason: the sender holds one request at a time, and calls
the response handler in the same task that awaited the answer.

`RungPlanner` turns "which rung is the monitor on right now" into the three
numbers one tick needs. The pipeline asks it every tick (E3), so a rung step
takes effect on the next frame without rebuilding anything -- and never
rebuilds a tracker (step-07-design H3).
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import Callable, Final

from cane.bridge.backends import BackendError, BackendTimeout, SwitchingBackend
from cane.bridge.config import BridgeConfig
from cane.bridge.health import Error, HealthMonitor, Ok, Timeout
from cane.bridge.rungs import Rung
from cane.cloud.api_models import InferenceMeta, InferenceRequest
from cane.contracts.awareness_event import Source
from cane.transport.wall_clock import now_ms

logger = logging.getLogger(__name__)

Clock = Callable[[], int]
#: An exception the backends never promised: still an outcome, never a crash.
UNEXPECTED_STATUS: Final = None


@dataclass(frozen=True, slots=True)
class RungPlan:
    """Everything one tick needs to know about the current operating point."""

    index: int
    crop_px: int
    jpeg_quality: int
    period_s: float


class RungPlanner:
    """`config.rung` unless a monitor says otherwise, with CLI pins honoured.

    `crop_px` and `send_fps` are explicit overrides (bench runs and tests
    pin them); when they are set, a rung step still changes the JPEG quality
    and the reported rung but leaves the pinned number alone, because the
    operator asked for it.
    """

    def __init__(
        self,
        config: BridgeConfig,
        monitor: HealthMonitor | None = None,
        crop_px: int | None = None,
    ) -> None:
        self._config = config
        self._monitor = monitor
        self._crop_px = crop_px

    @property
    def rung(self) -> Rung:
        if self._monitor is None:
            return self._config.rung
        return self._monitor.rung

    def current(self) -> RungPlan:
        rung = self.rung
        return RungPlan(
            index=rung.index,
            crop_px=self._crop_px if self._crop_px is not None else rung.crop_px,
            jpeg_quality=rung.jpeg_quality,
            period_s=(
                1.0 / self._config.send_fps
                if self._config.send_fps is not None
                else rung.period_s
            ),
        )


class MonitoredUplink:
    """`SwitchingBackend` wearing the sender's transport face."""

    def __init__(
        self,
        backend: SwitchingBackend,
        monitor: HealthMonitor,
        clock: Clock = now_ms,
    ) -> None:
        self._backend = backend
        self._monitor = monitor
        self._clock = clock
        self._last_source = backend.source
        self._last_meta: InferenceMeta | None = None

    @property
    def last_source(self) -> Source:
        """Who answered the most recent request. Read by the pipeline's
        response handler, which runs in the same task, one request later at
        the very most.
        """
        return self._last_source

    @property
    def last_meta(self) -> InferenceMeta | None:
        """The meta of the last request that reached a backend -- the rung
        that was actually used, for the stats line and for tests.
        """
        return self._last_meta

    async def post_crops(self, request: InferenceRequest) -> bytes:
        """Raises on failure, exactly like the HTTP transport: the sender
        counts and logs it, and the loop keeps running.

        The response is re-serialised so the sender's own parse -- including
        its `t_capture_ms` echo check -- still runs on both paths. That
        round trip costs microseconds on a few kB of JSON and happens after
        the answer, never inside the frame deadline.
        """
        backend = self._backend.current
        self._last_meta = request.meta
        started_ms = self._clock()
        try:
            response = await backend.infer(request.meta, request.crops)
        except asyncio.CancelledError:
            raise
        except BackendTimeout:
            self._monitor.observe(Timeout(), self._clock())
            raise
        except BackendError as exc:
            self._monitor.observe(Error(exc.status), self._clock())
            raise
        except Exception:  # noqa: BLE001 - an unpromised fault is still an outcome
            self._monitor.observe(Error(UNEXPECTED_STATUS), self._clock())
            raise
        self._last_source = backend.source
        self._monitor.observe(Ok(float(self._clock() - started_ms)), self._clock())
        return response.model_dump_json(by_alias=True).encode()

    async def aclose(self) -> None:
        await self._backend.aclose()
