"""The steering evidence, carried into the voice layer (plan step 26).

The voice layer must never tell the wearer "the left is clear" while the
haptic cue steers them right. The cheapest way to make that impossible is
not to test for it but to remove the second opinion: **what arrives here is
the ``steer`` frame the phone itself was sent**, so the words and the buzz
are rendered from one number.

That also settles how 48 depth columns are summarised for a language model:
they are not. ``steering.py`` already reduced the ring to the one bearing it
chose and the one bit that says whether to trust it as a path, and those two
values *are* the summary. Any per-sector average computed here would be a
second judgement and could disagree with the cue -- which is the whole
failure this step exists to prevent.

Two honesty properties come along for free:

* the bridge sends **no** ``steer`` frame in local-fallback mode or when the
  ring holds no evidence (plan Sec 4.2, ``steer_frame.py``'s three refusals),
  so a reading here is always a real measurement;
* a reading **expires**. A bridge that stopped sending has to make the voice
  layer fall silent about the ring, not quote the last one it saw.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from typing import Callable, Final, Protocol

from cane.transport.messages import STEER_TTL_MS, Steer

#: How old a reading may be and still be spoken. Derived from the steering
#: TTL rather than picked: five ticks of the 400 ms cue.
#:
#: The cue's own 400 ms is far too tight here and for a different reason --
#: it bounds a *continuous* signal driving an actuator on a walking wearer.
#: A spoken answer takes 1-3 s (step 20) and the wearer has been told to hold
#: still, so 2 s is honest for prose about a stationary scene. It is also
#: long enough to survive the 400 ms settle plus one 3 Hz tick, and short
#: enough that a bridge which flipped to local goes quiet inside the answer
#: it would otherwise poison.
CLEAR_WAY_MAX_AGE_MS: Final = 5 * STEER_TTL_MS


@dataclass(frozen=True, slots=True)
class ClearWay:
    """One tick's answer to "which way is freer, and is it a path at all?".

    ``bearing_deg`` is the bridge's ``gap_bearing_deg``: the chosen gap's
    centroid, or -- when ``blocked`` -- the least-bad column (plan Sec 3.1
    ruling 2). ``anchored`` is the frame's ``proximity_anchored``; it is
    false in the field (Sec 2.5) and carrying it is what lets the wording
    stay provably free of metres.
    """

    bearing_deg: float
    blocked: bool
    anchored: bool

    @classmethod
    def from_steer(cls, steer: Steer) -> ClearWay:
        """The frame the phone was sent, read as evidence for the voice layer."""
        return cls(
            bearing_deg=steer.gap_bearing_deg,
            blocked=steer.blocked,
            anchored=steer.proximity_anchored,
        )


class ClearWayProvider(Protocol):
    """What :class:`~cane.scene.service.SceneQueryService` asks for a ring.

    Deliberately shaped like ``frames.EquirectProvider``: one method, and
    ``None`` whenever there is nothing honest to hand over, so a bridge run
    without depth behaves exactly as step 20 did.
    """

    def latest_clear_way(self) -> ClearWay | None: ...


class LatestClearWay:
    """A single slot with an expiry, written by the bridge loop and read by
    the query task -- two different threads in the same process, so the swap
    is done under a lock.

    Never mutates a :class:`ClearWay`; publishing replaces the slot.
    """

    def __init__(
        self,
        *,
        max_age_ms: int = CLEAR_WAY_MAX_AGE_MS,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        self._max_age_s = max_age_ms / 1000.0
        self._monotonic = monotonic
        self._lock = threading.Lock()
        self._slot: tuple[ClearWay, float] | None = None

    def publish(self, way: ClearWay) -> None:
        stamped = (way, self._monotonic())
        with self._lock:
            self._slot = stamped

    def latest_clear_way(self) -> ClearWay | None:
        with self._lock:
            slot = self._slot
        if slot is None:
            return None
        way, stamped_at = slot
        if self._monotonic() - stamped_at > self._max_age_s:
            return None
        return way


class SteerSink(Protocol):
    """The bridge pipeline's steer sink, as seen from here."""

    async def send_steer(self, steer: Steer) -> bool: ...


class SteerTap:
    """A pass-through steer sink that also feeds a :class:`LatestClearWay`.

    Wiring rather than plumbing: the scene service already runs in the
    bridge's own process (``cane.bridge.__main__``), so the live ring is
    reachable by decorating the sink the pipeline already writes to -- no
    IPC, no second subscription, and no change to the pipeline or to the
    steering modules.

    The reading is published even when the phone did not take the frame: the
    ring was measured either way, and the wearer is asking the laptop.
    """

    def __init__(self, sink: SteerSink, store: LatestClearWay) -> None:
        self._sink = sink
        self._store = store

    async def send_steer(self, steer: Steer) -> bool:
        self._store.publish(ClearWay.from_steer(steer))
        return await self._sink.send_steer(steer)
