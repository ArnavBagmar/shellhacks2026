"""The signs this session has read, and how quickly their angles go stale.

The wearer's question is usually about something they cannot see any more --
"where was that restroom" is asked *after* walking past it. A one-shot look
at the current frame cannot answer that, so the scanner above this module
keeps publishing what it reads and this store keeps it for the session.

**The honest part is the ageing.** There is no odometry anywhere in this
system: no wheel count, no step count, and the phone's magnetometer heading
is deliberately walled off from the awareness path (``CaneNavigation.md``).
So a stored bearing is only where the sign *was* when it was last seen, and
it decays into a lie the moment the wearer turns a corner. Two windows keep
that from being spoken as fact:

* inside :data:`BEARING_TRUST_MAX_AGE_MS` the angle is offered, because the
  ring almost certainly still has the sign in one of its six crops and the
  bearing was re-published on the last scan;
* past it the place is still recalled -- it is genuinely useful to know a
  restroom is somewhere behind -- but **without** an angle, which the phone
  already speaks as "...nearby, direction unclear"
  (``ios/CaneKit/Sources/CaneAssist/AnswerPhrasing.swift``).

That decay is what makes a sign the wearer walked past migrate rearward on
its own: successive scans see it at 20 deg, then 95 deg, then 170 deg, and
each one replaces the angle rather than reasoning about motion.

Shaped after ``gap.LatestClearWay``: one lock, values never mutated in place,
and a reading that expires rather than being quoted forever. A clock is legal
here because this is the laptop, not the cloud (``camera-sync-design.md``).
"""

from __future__ import annotations

import threading
import time
from collections.abc import Mapping
from dataclasses import dataclass, replace
from typing import Callable, Final, Protocol

from cane.bridge.events import normalize_bearing_deg
from cane.scene.place_names import PlaceKind, classify, matches, normalize

#: How long a sighting is remembered at all. A hackathon session is under an
#: hour and the wearer stays in one building, so this is generous on purpose:
#: forgetting a corridor the wearer walked ten minutes ago would be worse
#: than offering it with its age attached.
PLACE_MAX_AGE_MS: Final = 15 * 60 * 1000

#: How long the stored *angle* stays evidence. Chosen as roughly how long a
#: sign stays somewhere in the 360 deg ring while the wearer walks away from
#: it at a normal pace -- past that the last angle is a memory, not a
#: measurement, and is withheld rather than guessed forward.
BEARING_TRUST_MAX_AGE_MS: Final = 20_000


@dataclass(frozen=True, slots=True)
class Sighting:
    """One sign, as the ring last read it.

    ``bearing_deg`` is torso-relative like everything else on the wire: zero
    ahead, negative to the left, folded into ``(-180, 180]``. ``seen_count``
    is how many scans have found this place, which is the cheapest available
    confidence signal -- a sign read five times is not a hallucination.
    """

    text: str
    kind: PlaceKind
    bearing_deg: float
    confidence: float
    seen_count: int

    @classmethod
    def seen(cls, *, text: str, bearing_deg: float, confidence: float) -> Sighting:
        """A first sighting: the kind is classified here so that callers
        cannot disagree about what a sign is."""
        return cls(
            text=text,
            kind=classify(text),
            bearing_deg=normalize_bearing_deg(bearing_deg),
            confidence=confidence,
            seen_count=1,
        )

    def merged_with(self, fresher: Sighting) -> Sighting:
        """This place, seen again: the new angle wins, the count carries over."""
        return replace(fresher, seen_count=self.seen_count + fresher.seen_count)


@dataclass(frozen=True, slots=True)
class Recall:
    """What memory can honestly say about a place right now.

    ``bearing_deg`` is ``None`` when the sighting is too old for its angle to
    be evidence; ``sighting.bearing_deg`` still holds what was seen, for the
    log. ``age_ms`` is how long ago it was last seen, so the answer can say.
    """

    sighting: Sighting
    age_ms: int
    bearing_deg: float | None


class PlaceProvider(Protocol):
    """What :class:`~cane.scene.service.SceneQueryService` asks of memory.

    Shaped like ``gap.ClearWayProvider``: one method, and ``None`` whenever
    there is nothing honest to hand over, so a run with the scanner switched
    off behaves exactly as step 20 did.
    """

    def recall(self, target: str) -> Recall | None: ...


class PlaceMemory:
    """Signs read this session, written by the scanner task and read by the
    query task -- two different tasks in one process, so the swap is done
    under a lock.

    Never mutates a :class:`Sighting` or the mapping; publishing replaces
    both.
    """

    def __init__(
        self,
        *,
        max_age_ms: int = PLACE_MAX_AGE_MS,
        bearing_trust_ms: int = BEARING_TRUST_MAX_AGE_MS,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        self._max_age_s = max_age_ms / 1000.0
        self._bearing_trust_s = bearing_trust_ms / 1000.0
        self._monotonic = monotonic
        self._lock = threading.Lock()
        self._places: Mapping[str, tuple[Sighting, float]] = {}

    def publish(self, sighting: Sighting) -> None:
        """Record a sighting, merging it into the place it names.

        A sign whose words normalize to nothing is not a place and is
        dropped: it would otherwise become an entry no target can name and
        every empty target could.
        """
        key = normalize(sighting.text)
        if not key:
            return
        stamped_at = self._monotonic()
        with self._lock:
            previous = self._places.get(key)
            merged = sighting if previous is None else previous[0].merged_with(sighting)
            self._places = {**self._places, key: (merged, stamped_at)}

    def recall(self, target: str) -> Recall | None:
        """The freshest remembered place matching ``target``, if any."""
        live = self._live()
        best: tuple[Sighting, float] | None = None
        for sighting, stamped_at in live.values():
            if not matches(target, sighting.text):
                continue
            if best is None or stamped_at > best[1]:
                best = (sighting, stamped_at)
        if best is None:
            return None

        sighting, stamped_at = best
        age_s = self._monotonic() - stamped_at
        return Recall(
            sighting=sighting,
            age_ms=int(round(age_s * 1000.0)),
            bearing_deg=sighting.bearing_deg if age_s <= self._bearing_trust_s else None,
        )

    def places(self) -> tuple[Sighting, ...]:
        """Every place still remembered, for logging and the debug view."""
        return tuple(sighting for sighting, _ in self._live().values())

    def _live(self) -> Mapping[str, tuple[Sighting, float]]:
        """A snapshot with expired places dropped, pruning the store as it goes."""
        now = self._monotonic()
        with self._lock:
            kept = {
                key: value
                for key, value in self._places.items()
                if now - value[1] <= self._max_age_s
            }
            if len(kept) != len(self._places):
                self._places = kept
            return kept
