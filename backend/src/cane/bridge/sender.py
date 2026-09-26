"""One request in flight, EVER -- rule 2 of design Sec 3.3.

If the uplink is slower than the send rate, queued requests make latency grow
without bound and nothing in the logs says so: the warnings simply arrive
late. So this sender holds exactly one slot. A frame that arrives while the
slot is occupied is *dropped*, which bounds frame age structurally instead of
hoping the network cooperates.

Every request that is actually sent records ``frame_age_at_send_ms`` (now
minus the frame's arrival on the laptop) and its RTT into bounded rolling
windows -- that p95 is the step's acceptance number.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from enum import StrEnum
from typing import Awaitable, Callable, Protocol

from pydantic import ValidationError

from cane.bridge.metrics import RollingWindow
from cane.cloud.api_models import InferenceRequest, InferenceResponse
from cane.transport.wall_clock import now_ms

logger = logging.getLogger(__name__)

STATS_WINDOW = 100
MAX_IN_FLIGHT = 1
RESPONSE_PREVIEW_CHARS = 200


class SendOutcome(StrEnum):
    """What ``try_send`` did with this frame."""

    SENT = "sent"
    DROPPED_BUSY = "dropped_busy"


class InferenceTransport(Protocol):
    """Whatever actually moves bytes to the inference service."""

    async def post_crops(self, request: InferenceRequest) -> bytes: ...

    async def aclose(self) -> None: ...


ResponseHandler = Callable[[InferenceResponse], Awaitable[None]]


@dataclass(frozen=True)
class SenderStats:
    """An immutable snapshot; ``None`` percentiles mean "no sample yet"."""

    sent: int
    dropped_busy: int
    succeeded: int
    failed: int
    rejected: int
    in_flight: int
    max_in_flight: int
    frame_age_p50_ms: float | None
    frame_age_p95_ms: float | None
    rtt_p50_ms: float | None
    rtt_p95_ms: float | None


class OneInFlightSender:
    """``try_send`` never blocks and never queues."""

    def __init__(
        self,
        transport: InferenceTransport,
        on_response: ResponseHandler | None = None,
        window: int = STATS_WINDOW,
    ) -> None:
        self._transport = transport
        self._on_response = on_response
        self._task: asyncio.Task[None] | None = None
        self._frame_age_ms = RollingWindow(window)
        self._rtt_ms = RollingWindow(window)
        self._sent = 0
        self._dropped_busy = 0
        self._succeeded = 0
        self._failed = 0
        self._rejected = 0
        self._max_in_flight = 0

    @property
    def in_flight(self) -> int:
        return 1 if self._task is not None and not self._task.done() else 0

    @property
    def busy(self) -> bool:
        return self.in_flight > 0

    @property
    def stats(self) -> SenderStats:
        return SenderStats(
            sent=self._sent,
            dropped_busy=self._dropped_busy,
            succeeded=self._succeeded,
            failed=self._failed,
            rejected=self._rejected,
            in_flight=self.in_flight,
            max_in_flight=self._max_in_flight,
            frame_age_p50_ms=self._frame_age_ms.p50,
            frame_age_p95_ms=self._frame_age_ms.p95,
            rtt_p50_ms=self._rtt_ms.p50,
            rtt_p95_ms=self._rtt_ms.p95,
        )

    def try_send(self, request: InferenceRequest, t_arrival_laptop_ms: int) -> SendOutcome:
        """Start the request, or drop this frame if one is still pending."""
        if self.busy:
            self._dropped_busy += 1
            return SendOutcome.DROPPED_BUSY
        frame_age_ms = now_ms() - t_arrival_laptop_ms
        self._frame_age_ms.add(float(frame_age_ms))
        self._sent += 1
        self._task = asyncio.get_running_loop().create_task(self._run(request))
        self._max_in_flight = max(self._max_in_flight, self.in_flight)
        return SendOutcome.SENT

    async def drain(self) -> None:
        """Await the pending request, if any. Never raises: the task swallows
        and counts its own failures. Shielded so that cancelling the waiter
        (a shutdown) does not cancel a request that is already on the wire.
        """
        task = self._task
        if task is None or task is asyncio.current_task():
            return
        await asyncio.shield(task)

    async def aclose(self) -> None:
        await self.drain()
        await self._transport.aclose()

    async def _run(self, request: InferenceRequest) -> None:
        started_ms = now_ms()
        try:
            raw = await self._transport.post_crops(request)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - one bad request must not kill the loop
            self._failed += 1
            logger.warning(
                "inference request failed (%s: %s); dropping this frame's detections",
                type(exc).__name__,
                exc,
            )
            return
        self._rtt_ms.add(float(now_ms() - started_ms))
        response = self._parse(raw, request)
        if response is None:
            return
        self._succeeded += 1
        if self._on_response is None:
            return
        try:
            await self._on_response(response)
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 - a handler fault must not kill the loop
            logger.exception("the inference response handler raised; continuing")

    def _parse(self, raw: bytes, request: InferenceRequest) -> InferenceResponse | None:
        try:
            response = InferenceResponse.model_validate_json(raw)
        except ValidationError as exc:
            self._rejected += 1
            logger.warning(
                "rejected an inference response: %s (body starts: %r)",
                exc,
                raw[:RESPONSE_PREVIEW_CHARS],
            )
            return None
        if response.t_capture_ms != request.meta.t_capture_ms:
            self._rejected += 1
            logger.warning(
                "rejected an inference response: t_capture_ms %d was not echoed (sent %d)",
                response.t_capture_ms,
                request.meta.t_capture_ms,
            )
            return None
        return response
