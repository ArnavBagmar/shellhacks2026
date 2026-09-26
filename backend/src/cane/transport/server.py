"""The Python side of the WebSocket transport: laptop as server (D1).

Serves exactly one phone at a time; a second incoming connection replaces
the first (logged, D4). Runs a ping loop every ``ping_interval_s`` and
folds each pong into an :class:`OffsetEstimator` -- the server holds the
latest estimator instance, replacing it wholesale on every update rather
than mutating it, per the estimator's own immutability contract.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from types import TracebackType
from typing import Callable

import websockets
from websockets.asyncio.server import Server, ServerConnection

from cane.contracts.awareness_event import AwarenessEvent
from cane.transport.clock_sync import PING_INTERVAL_S, NtpSample, OffsetEstimator, PhoneClock
from cane.transport.messages import (
    EventMessage,
    MalformedMessageError,
    PhoneHealth,
    Ping,
    Pong,
    Unknown,
    decode,
    encode,
)
from cane.transport.wall_clock import now_ms

logger = logging.getLogger(__name__)


class EventLinkServer:
    """``async with EventLinkServer(host, port, ping_interval_s, pipeline_delay_d_ms) as srv:``

    Exposes ``srv.phone_clock`` (a fresh :class:`PhoneClock` snapshotting
    the current estimator) and ``await srv.send_event(event)``.
    """

    def __init__(
        self,
        host: str,
        port: int,
        ping_interval_s: float = PING_INTERVAL_S,
        pipeline_delay_d_ms: int = 0,
        on_health: Callable[[PhoneHealth], None] | None = None,
    ) -> None:
        self._host = host
        self._port = port
        self._ping_interval_s = ping_interval_s
        self._pipeline_delay_d_ms = pipeline_delay_d_ms
        # Step 8's health monitor, when there is one. Synchronous by
        # design: folding one report into an immutable state is a handful
        # of integer compares, and the inbound frame loop must not await
        # anything that could stall the pong stream behind it.
        self._on_health = on_health
        self._estimator = OffsetEstimator()
        self._connection: ServerConnection | None = None
        self._server: Server | None = None
        self._ping_task: asyncio.Task[None] | None = None

    @property
    def port(self) -> int:
        """The bound TCP port -- the OS-assigned one if constructed with
        ``port=0``. Only valid inside the ``async with`` block.
        """
        if self._server is None:
            raise RuntimeError("EventLinkServer.port: server is not running (use 'async with')")
        return self._server.sockets[0].getsockname()[1]

    @property
    def phone_clock(self) -> PhoneClock:
        return PhoneClock.from_estimator(self._estimator, self._pipeline_delay_d_ms)

    async def __aenter__(self) -> EventLinkServer:
        self._server = await websockets.serve(self._handle_connection, self._host, self._port)
        self._ping_task = asyncio.create_task(self._ping_loop())
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        if self._ping_task is not None:
            self._ping_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._ping_task
        if self._server is not None:
            self._server.close()
            await self._server.wait_closed()

    async def send_event(self, event: AwarenessEvent) -> bool:
        """Send one event frame to the connected phone.

        Never raises: a missing or dropped connection is logged and
        reported as ``False`` (D4), so a caller's demo/production loop
        never crashes on a disconnect.
        """
        connection = self._connection
        if connection is None:
            logger.warning("send_event: no phone connected")
            return False
        try:
            await connection.send(encode(EventMessage(event=event)))
        except websockets.ConnectionClosed:
            logger.warning("send_event: connection closed mid-send")
            # Only clear if `connection` is still the *current* one: a
            # second phone may already have replaced it (D4) while this
            # send was in flight, and clearing unconditionally would drop
            # that new, healthy connection instead of the dead one this
            # call actually observed.
            if self._connection is connection:
                self._connection = None
            return False
        return True

    async def _handle_connection(self, connection: ServerConnection) -> None:
        if self._connection is not None:
            logger.info("second phone connection arrived; replacing the first")
            with contextlib.suppress(websockets.ConnectionClosed):
                await self._connection.close()
        self._connection = connection
        try:
            async for raw in connection:
                self._handle_inbound_frame(raw)
        except websockets.ConnectionClosed:
            pass
        finally:
            if self._connection is connection:
                self._connection = None

    def _handle_inbound_frame(self, raw: str | bytes) -> None:
        text = raw if isinstance(raw, str) else raw.decode("utf-8")
        try:
            msg = decode(text)
        except MalformedMessageError as exc:
            logger.warning("dropping malformed frame from phone: %s", exc)
            return

        if isinstance(msg, Pong):
            self._apply_pong(msg)
        elif isinstance(msg, PhoneHealth):
            self._apply_health(msg)
        elif isinstance(msg, Unknown):
            logger.debug("ignoring unknown frame type from phone: %s", msg.type)
        else:
            # Ping/EventMessage from the phone is outside D1's protocol
            # (both flow laptop -> phone only); never worth dropping the
            # connection over, so it is logged and ignored like Unknown.
            logger.debug("ignoring unexpected %s frame from phone", type(msg).__name__)

    def _apply_health(self, health: PhoneHealth) -> None:
        """The phone's stale-drop signal (Sec 4.4). A hook that raises is a
        bug in the monitor, never a reason to drop the phone -- the link is
        the only thing carrying warnings to the wearer.
        """
        logger.debug(
            "phone health: %d/%d dropped stale (rate %.2f over %d buckets)",
            health.stale_dropped, health.received, health.stale_drop_rate,
            health.complete_buckets,
        )
        if self._on_health is None:
            return
        try:
            self._on_health(health)
        except Exception:  # noqa: BLE001 - the connection outlives the hook
            logger.exception("the on_health hook raised; ignoring this report")

    def _apply_pong(self, pong: Pong) -> None:
        t4 = now_ms()
        sample = NtpSample(t1=pong.t1, t2=pong.t2, t3=pong.t3, t4=t4)
        self._estimator = self._estimator.add(sample)
        logger.debug("pong -> offset=%.1fms rtt=%dms", sample.offset_ms, sample.rtt_ms)

    async def _ping_loop(self) -> None:
        while True:
            await asyncio.sleep(self._ping_interval_s)
            connection = self._connection
            if connection is None:
                continue
            try:
                await connection.send(encode(Ping(t1=now_ms())))
            except websockets.ConnectionClosed:
                logger.warning("ping: connection closed mid-send")
                # Same identity guard as send_event: don't clear a
                # replacement connection that arrived while this ping was
                # in flight.
                if self._connection is connection:
                    self._connection = None


if __name__ == "__main__":
    # `uv run python -m cane.transport.server` (D4) delegates to the CLI in
    # `__main__.py` so the flag-parsing/demo-loop logic lives in exactly
    # one place.
    from cane.transport.__main__ import main

    main()
