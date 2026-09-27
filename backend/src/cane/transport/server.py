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
from typing import Awaitable, Callable

import websockets
from websockets.asyncio.server import Server, ServerConnection

from cane.contracts.awareness_event import AwarenessEvent
from cane.transport.clock_sync import PING_INTERVAL_S, NtpSample, OffsetEstimator, PhoneClock
from cane.transport.log_throttle import LogThrottle
from cane.transport.messages import (
    Ground,
    EventMessage,
    GuideUpdate,
    MalformedMessageError,
    PhoneDiagnostic,
    PhoneHealth,
    Ping,
    Pong,
    SceneAnswer,
    SceneQuery,
    SpeakRequest,
    Steer,
    VoiceClip,
    Unknown,
    decode,
    encode,
)
from cane.transport.wall_clock import now_ms

logger = logging.getLogger(__name__)
#: The phone's black-box recorder lines, one JSON object per log record, so a
#: file handler on this logger alone yields a clean JSONL of the walk.
phone_logger = logging.getLogger("cane.phone")

#: Both of the events below fire once per frame when they fire at all -- a
#: phone from another build, or a phone that is not there. One line per
#: window each; the counters beside them stay exact.
IGNORED_LOG_INTERVAL_S = 5.0
#: A ``query`` frame carries one base64 phone JPEG (<= ~2.8 MB of text);
#: the websockets default of 1 MiB would close the connection on it.
MAX_FRAME_BYTES = 4 * 1024 * 1024

QueryHandler = Callable[[SceneQuery], Awaitable[None]]
SpeakHandler = Callable[[SpeakRequest], Awaitable[VoiceClip]]


def _listen_hosts(host: str) -> str | list[str]:
    """``0.0.0.0`` ("every interface") also listens on IPv6. A T-Mobile
    phone hotspot is IPv6-only (2026-09-26): the laptop's only IPv4 address
    there is 464XLAT's private 192.0.0.2, which the phone cannot reach, so
    the phone must connect over IPv6.
    """
    return [host, "::"] if host == "0.0.0.0" else host


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
        on_query: QueryHandler | None = None,
        on_speak: SpeakHandler | None = None,
        on_guide: Callable[[GuideUpdate], None] | None = None,
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
        # Step 20's scene service. Async and slow (a VLM call), so it runs
        # as its own task: the inbound loop keeps answering pongs meanwhile.
        self._on_query = on_query
        # The natural voice (ElevenLabs). Every ``speak`` gets exactly one
        # ``voice`` back -- ``unavailable`` without a hook -- so the phone
        # never waits out a timeout before using its own voice.
        self._on_speak = on_speak
        # "Guide my hand": the phone's live tracking, for the laptop page.
        # Synchronous and display-only (it stores the newest update).
        self._on_guide = on_guide
        self._query_tasks: set[asyncio.Task[None]] = set()
        self._estimator = OffsetEstimator()
        self._frames_ignored = 0
        self._events_dropped_no_phone = 0
        self._ignored_log = LogThrottle(IGNORED_LOG_INTERVAL_S)
        self._no_phone_log = LogThrottle(IGNORED_LOG_INTERVAL_S)
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
    def frames_ignored(self) -> int:
        """Inbound frames this build had no use for -- an unknown type, or
        one that only flows the other way. Surfaced on the bridge's 5 s
        stats line: a phone talking a protocol the laptop does not answer
        otherwise looks exactly like a phone saying nothing.
        """
        return self._frames_ignored

    @property
    def events_dropped_no_phone(self) -> int:
        """Events that had nowhere to go. The warning about them is
        throttled; this number is not.
        """
        return self._events_dropped_no_phone

    @property
    def phone_clock(self) -> PhoneClock:
        return PhoneClock.from_estimator(self._estimator, self._pipeline_delay_d_ms)

    async def __aenter__(self) -> EventLinkServer:
        self._server = await websockets.serve(
            self._handle_connection, _listen_hosts(self._host), self._port, max_size=MAX_FRAME_BYTES
        )
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
        for task in list(self._query_tasks):
            task.cancel()
        if self._query_tasks:
            await asyncio.gather(*self._query_tasks, return_exceptions=True)
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
            self._count_no_phone()
            return False
        return await self._send_on(connection, encode(EventMessage(event=event)), "send_event")

    async def send_answer(self, answer: SceneAnswer) -> bool:
        """Send one ``answer`` frame to the connected phone. Never raises,
        like :meth:`send_event`; ``False`` when there is no phone.
        """
        connection = self._connection
        if connection is None:
            logger.warning("send_answer: no phone connected; answer %s dropped", answer.query_id)
            return False
        return await self._send_on(connection, encode(answer), "send_answer")

    async def send_steer(self, steer: Steer) -> bool:
        """Send one ``steer`` frame to the connected phone (step 23 D).

        The same route and the same never-raises contract as
        :meth:`send_event`. Not counted or warned about per frame: steering
        rides every tick at rung fps, and the bridge already counts what it
        could not deliver -- a second throttled warning here would only
        duplicate the one :meth:`send_event` is already printing about the
        same missing phone.
        """
        connection = self._connection
        if connection is None:
            return False
        return await self._send_on(connection, encode(steer), "send_steer")

    async def send_ground(self, ground: Ground) -> bool:
        """Send one ``ground`` frame to the connected phone (step 29).

        The same route and the same never-raises contract as
        :meth:`send_steer`. Rarer than steering by design -- a frame only
        exists when there is actually a hole -- so it is not throttled.
        """
        connection = self._connection
        if connection is None:
            return False
        return await self._send_on(connection, encode(ground), "send_ground")

    async def _send_on(self, connection: ServerConnection, text: str, what: str) -> bool:
        try:
            await connection.send(text)
        except websockets.ConnectionClosed:
            logger.warning("%s: connection closed mid-send", what)
            # Only clear if `connection` is still the *current* one: a
            # second phone may already have replaced it (D4) while this
            # send was in flight, and clearing unconditionally would drop
            # that new, healthy connection instead of the dead one this
            # call actually observed.
            if self._connection is connection:
                self._connection = None
            return False
        return True

    def _count_no_phone(self) -> None:
        """One line per window, carrying the running total: at 3 fps a walk
        with no phone attached would otherwise write hundreds of identical
        warnings and hide everything else.
        """
        self._events_dropped_no_phone += 1
        if not self._no_phone_log.allow():
            return
        logger.warning(
            "send_event: no phone connected; %d event(s) dropped so far",
            self._events_dropped_no_phone,
        )

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
        elif isinstance(msg, PhoneDiagnostic):
            phone_logger.info(msg.model_dump_json())
        elif isinstance(msg, SceneQuery):
            self._apply_query(msg)
        elif isinstance(msg, SpeakRequest):
            self._spawn(self._run_speak(msg))
        elif isinstance(msg, GuideUpdate):
            self._apply_guide(msg)
        elif isinstance(msg, Unknown):
            self._count_ignored(msg.type)
        else:
            # Ping/EventMessage from the phone is outside D1's protocol
            # (both flow laptop -> phone only); never worth dropping the
            # connection over, so it is counted and ignored like Unknown.
            self._count_ignored(type(msg).__name__)

    def _count_ignored(self, frame_type: str) -> None:
        """A frame this build does not act on. INFO, not DEBUG: a phone
        from a newer build talking past the laptop is a version skew the
        operator needs to see, and at DEBUG nobody ever does.
        """
        self._frames_ignored += 1
        if not self._ignored_log.allow():
            return
        logger.info(
            "ignoring %r frame(s) from the phone: %d frame(s) not acted on so far",
            frame_type,
            self._frames_ignored,
        )

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

    def _apply_guide(self, update: GuideUpdate) -> None:
        """The hand guide's live state. Only a page draws it; with no page it
        is counted as ignored, and a hook that raises never drops the phone.
        """
        if self._on_guide is None:
            self._count_ignored("guide")
            return
        try:
            self._on_guide(update)
        except Exception:  # noqa: BLE001 - the connection outlives the hook
            logger.exception("the on_guide hook raised; ignoring this update")

    def _apply_query(self, query: SceneQuery) -> None:
        """Hand a spoken question to the scene service on its own task. With
        no service attached the frame is counted as ignored: the phone's own
        timeout then tells the wearer the feature is unavailable.
        """
        if self._on_query is None:
            self._count_ignored("query")
            return
        logger.info("query %s (%s %r)", query.query_id, query.kind, query.target)
        task = asyncio.create_task(self._run_query(self._on_query, query))
        self._query_tasks.add(task)
        task.add_done_callback(self._query_tasks.discard)

    def _spawn(self, work: Awaitable[None]) -> None:
        task = asyncio.ensure_future(work)
        self._query_tasks.add(task)
        task.add_done_callback(self._query_tasks.discard)

    async def _run_speak(self, request: SpeakRequest) -> None:
        clip = VoiceClip.unavailable(request.speak_id)
        if self._on_speak is not None:
            try:
                clip = await self._on_speak(request)
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001 - the phone falls back to its own voice
                logger.exception("the on_speak hook raised for %s", request.speak_id)
        await self.send_voice(clip)

    async def send_voice(self, clip: VoiceClip) -> bool:
        """Send one ``voice`` frame. Never raises; ``False`` with no phone."""
        connection = self._connection
        if connection is None:
            return False
        return await self._send_on(connection, encode(clip), "send_voice")

    @staticmethod
    async def _run_query(handler: QueryHandler, query: SceneQuery) -> None:
        try:
            await handler(query)
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 - the connection outlives the hook
            logger.exception("the on_query hook raised for %s", query.query_id)

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
