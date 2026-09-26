"""EventLinkServer integration tests -- real sockets on 127.0.0.1.

Each test binds to port 0 (an OS-assigned free port) and connects a fake
phone client with the real ``websockets`` client library, exercising the
handshake and event delivery end to end (D6).
"""

import asyncio

import websockets

from cane.transport.messages import EventMessage, Ping, Pong, decode, encode
from cane.transport.server import EventLinkServer

RECV_TIMEOUT_S = 2.0


async def _recv_decoded(connection):
    raw = await asyncio.wait_for(connection.recv(), timeout=RECV_TIMEOUT_S)
    return decode(raw)


async def test_pong_syncs_phone_clock_with_expected_offset():
    async with EventLinkServer("127.0.0.1", 0, ping_interval_s=0.05) as srv:
        async with websockets.connect(f"ws://127.0.0.1:{srv.port}") as phone:
            ping = await _recv_decoded(phone)
            assert isinstance(ping, Ping)

            # Fake phone clock: laptop_now + 250 (task-3-brief.md D6).
            phone_now = ping.t1 + 250
            await phone.send(encode(Pong(t1=ping.t1, t2=phone_now, t3=phone_now)))

            for _ in range(50):
                if srv.phone_clock.estimator.is_synced:
                    break
                await asyncio.sleep(0.02)

            assert srv.phone_clock.estimator.is_synced
            assert abs(srv.phone_clock.estimator.offset_ms - 250) < 20


async def test_send_event_delivers_frame_phone_decodes_to_equal_event(golden_valid_event):
    async with EventLinkServer("127.0.0.1", 0, ping_interval_s=0.05) as srv:
        async with websockets.connect(f"ws://127.0.0.1:{srv.port}") as phone:
            # Wait for the first ping: proof the server has registered this
            # connection before we ask it to send_event.
            await _recv_decoded(phone)

            delivered = await srv.send_event(golden_valid_event)
            assert delivered is True

            msg = await _recv_decoded(phone)
            assert isinstance(msg, EventMessage)
            assert msg.event == golden_valid_event


async def test_garbage_frame_from_phone_does_not_kill_connection():
    async with EventLinkServer("127.0.0.1", 0, ping_interval_s=0.05) as srv:
        async with websockets.connect(f"ws://127.0.0.1:{srv.port}") as phone:
            await _recv_decoded(phone)  # first ping: connection is live

            await phone.send("not json at all {")

            # The connection must stay up: a second ping still arrives.
            second_ping = await _recv_decoded(phone)
            assert isinstance(second_ping, Ping)


async def test_send_event_with_no_client_returns_false(golden_valid_event):
    async with EventLinkServer("127.0.0.1", 0, ping_interval_s=5.0) as srv:
        delivered = await srv.send_event(golden_valid_event)
        assert delivered is False


class _StaleConnection:
    """A stand-in for a connection object that is about to fail mid-send.

    `send()` blocks until the test says it may proceed, so the test can
    deterministically arrange for a *replacement* connection to take over
    ``srv._connection`` while this stale one is still "in flight" inside
    ``send_event`` -- reproducing the exact race in review finding #5
    without depending on real-socket timing.
    """

    def __init__(self) -> None:
        self.about_to_send = asyncio.Event()
        self.allowed_to_fail = asyncio.Event()

    async def send(self, _data: str) -> None:
        self.about_to_send.set()
        await self.allowed_to_fail.wait()
        raise websockets.ConnectionClosedOK(None, None)

    async def close(self) -> None:
        """No-op: `_handle_connection` closes the outgoing connection when
        a replacement arrives (D4); this stand-in has nothing real to
        close.
        """


async def test_send_event_failure_on_stale_connection_does_not_clobber_replacement(golden_valid_event):
    async with EventLinkServer("127.0.0.1", 0, ping_interval_s=0.05) as srv:
        stale = _StaleConnection()
        srv._connection = stale  # simulate: phone A is the current connection

        send_to_stale = asyncio.create_task(srv.send_event(golden_valid_event))
        await asyncio.wait_for(stale.about_to_send.wait(), timeout=RECV_TIMEOUT_S)
        # send_event is now suspended inside stale.send(), exactly as it
        # would be mid-network-write to a phone that is about to drop.

        async with websockets.connect(f"ws://127.0.0.1:{srv.port}") as phone_b:
            # Phone B connecting replaces the stale connection -- the
            # same "second connection replaces the first" path D4
            # describes -- while send_event's call above is still
            # suspended on the old one.
            await _recv_decoded(phone_b)  # first ping: B is now srv._connection

            stale.allowed_to_fail.set()  # let the stale send fail now
            delivered_to_stale = await asyncio.wait_for(send_to_stale, timeout=RECV_TIMEOUT_S)
            assert delivered_to_stale is False

            # The bug: without an identity guard, handling that failure
            # would have cleared `srv._connection` unconditionally,
            # clobbering B even though B is healthy and current.
            delivered_to_b = await srv.send_event(golden_valid_event)
            assert delivered_to_b is True

            msg = await _recv_decoded(phone_b)
            assert isinstance(msg, EventMessage)
            assert msg.event == golden_valid_event
