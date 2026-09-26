"""Frame pickup: take a later instant of reality when it is cheap to wait.

A 30 fps slot holds a frame that is on average 17 ms old, so a tick that is
about to spend ~6 ms dewarping can often trade a few milliseconds of waiting
for pixels that describe a *later* moment. That trade is only worth making if
the wait is bounded: the retry looks for a frame that is at most one grab
period away, so it must not reuse the long no-frame timeout.
"""

from __future__ import annotations

import time

import numpy as np

from cane.bridge.capture.base import Frame
from cane.bridge.config import BridgeConfig
from cane.bridge.deadline import FRAME_DEADLINE_MS
from cane.bridge.pipeline import (
    FRAME_WAIT_TIMEOUT_S,
    FRESHNESS_RETRY_TIMEOUT_S,
    MAX_FRAME_AGE_AT_PICKUP_MS,
    BridgePipeline,
    LaptopClock,
)
from cane.bridge.sender import OneInFlightSender
from cane.transport.wall_clock import now_ms

STALE_PICKUP_AGE_MS = 25
FRESH_PICKUP_AGE_MS = 5
RETRY_BUDGET_S = 0.1
IMAGE = np.zeros((4, 8, 3), dtype=np.uint8)

#: Scripted reply meaning "no frame arrived before the timeout expired",
#: which is what ``LatestFrameStore.wait_for_new`` does: it blocks for the
#: whole timeout and then returns ``None``.
TIMES_OUT = object()


class ScriptedGrab:
    """A grab thread stand-in that answers waits from a fixed script."""

    def __init__(self, *replies: object) -> None:
        self._replies = replies
        self.waits: list[tuple[int, float]] = []
        self.frames_grabbed = 0

    def wait_for_new(self, last_seq: int, timeout_s: float) -> Frame | None:
        self.waits.append((last_seq, timeout_s))
        index = min(len(self.waits) - 1, len(self._replies) - 1)
        reply = self._replies[index]
        if reply is TIMES_OUT:
            time.sleep(timeout_s)
            return None
        assert reply is None or isinstance(reply, Frame)
        return reply


class UnusedTransport:
    async def post_crops(self, request: object) -> bytes:  # pragma: no cover - never called
        raise AssertionError("pickup tests never reach the wire")

    async def aclose(self) -> None:  # pragma: no cover - never called
        return None


def _frame(age_ms: int, seq: int) -> Frame:
    return Frame(image=IMAGE, t_arrival_laptop_ms=now_ms() - age_ms, seq=seq)


def _pipeline(grab: ScriptedGrab) -> BridgePipeline:
    return BridgePipeline(
        config=BridgeConfig(session_id="freshest-test"),
        grab=grab,
        sender=OneInFlightSender(UnusedTransport()),
        clock_provider=LaptopClock,
        event_sink=None,
    )


def test_a_fresher_frame_arriving_during_the_retry_wins() -> None:
    stale = _frame(STALE_PICKUP_AGE_MS, seq=7)
    fresher = _frame(0, seq=8)
    grab = ScriptedGrab(stale, fresher)

    picked = _pipeline(grab)._freshest_frame()

    assert picked is fresher
    assert len(grab.waits) == 2
    # The retry asks for something newer than the frame it already holds.
    assert grab.waits[1][0] == stale.seq


def test_the_retry_is_bounded_and_falls_back_to_the_frame_in_hand() -> None:
    """A stalled camera must cost one grab period, not the no-frame timeout.

    Reusing ``FRAME_WAIT_TIMEOUT_S`` here would burn 250 ms per tick chasing
    a frame that is not coming, and the frame finally sent would be 250 ms
    older than the one already in hand.
    """
    stale = _frame(STALE_PICKUP_AGE_MS, seq=3)
    grab = ScriptedGrab(stale, TIMES_OUT)

    started = time.monotonic()
    picked = _pipeline(grab)._freshest_frame()
    elapsed_s = time.monotonic() - started

    assert picked is stale
    assert elapsed_s < RETRY_BUDGET_S, f"the retry waited {elapsed_s * 1000:.0f} ms"
    assert grab.waits[1][1] == FRESHNESS_RETRY_TIMEOUT_S
    assert FRESHNESS_RETRY_TIMEOUT_S < FRAME_WAIT_TIMEOUT_S
    # The fallback frame is still worth sending.
    assert now_ms() - picked.t_arrival_laptop_ms < FRAME_DEADLINE_MS


def test_an_already_fresh_frame_is_taken_without_a_retry() -> None:
    fresh = _frame(FRESH_PICKUP_AGE_MS, seq=1)
    assert FRESH_PICKUP_AGE_MS <= MAX_FRAME_AGE_AT_PICKUP_MS
    grab = ScriptedGrab(fresh, TIMES_OUT)

    picked = _pipeline(grab)._freshest_frame()

    assert picked is fresh
    assert grab.waits == [(0, FRAME_WAIT_TIMEOUT_S)]


def test_no_frame_at_all_is_not_a_retry() -> None:
    grab = ScriptedGrab(None)

    assert _pipeline(grab)._freshest_frame() is None
    assert len(grab.waits) == 1
