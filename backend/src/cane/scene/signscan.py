"""The background sweep that reads the building's signage into memory.

Nobody asks for this. It runs for the life of the walk so that a sign the
wearer strolls past is already remembered by the time they think to ask about
it -- which is the whole difference between this and the step 20 one-shot
look, and the reason the 360 ring is the right sensor for wayfinding: the
rear crop reads the plate nobody aimed at.

**Why it is a background task and not a tick hook.** The bridge's tick is
deadline-guarded: a frame older than ``FRAME_DEADLINE_MS`` is dropped and all
the cv2 work happens inside ``asyncio.to_thread`` (``bridge/pipeline.py``).
A model call takes 1-3 s, so nothing VLM-shaped can live in there. The shape
copied here is ``CloudProbe``: one long-lived task owned by ``run_bridge``,
cancelled in its ``finally``, entirely off the safety path.

**Three refusals, all for the same reason.** A sign with no readable words,
a sign in an image index that does not exist, and a reading the model itself
called low confidence are all dropped rather than stored. A wrong room number
does not merely fail to help -- it walks a blind person to the wrong door, so
silence is strictly cheaper than a guess.

**It yields to the wearer.** ``SceneQueryService`` already supersedes an
in-flight question with a newer one; a sweep must not be the thing competing
for that slot, so ``busy`` is checked before any call is made.
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
from collections.abc import Awaitable, Callable, Sequence
from typing import TYPE_CHECKING, Final

from cane.scene.place_names import normalize
from cane.scene.places import PlaceMemory, Sighting
from cane.scene.prompt import MAX_SIGNS, build_scan_prompt
from cane.scene.vlm import ScanReply, SignScanningModel

if TYPE_CHECKING:  # pragma: no cover - typing only
    from cane.scene.service import View

logger = logging.getLogger(__name__)

#: Off switch, in the house idiom. Turning the scanner off leaves the step 20
#: one-shot query path behaving exactly as it did before this feature.
SIGN_SCAN_ENV: Final = "CANE_SIGN_SCAN"
#: Seconds between sweeps. At a walking pace of about 1.2 m/s this is roughly
#: every five metres, which is often enough that a sign stays in memory with a
#: fresh bearing while it is still somewhere in the ring, and rare enough that
#: a walk does not burn the wearer's model quota.
SIGN_SCAN_INTERVAL_ENV: Final = "CANE_SIGN_SCAN_INTERVAL_S"
DEFAULT_SCAN_INTERVAL_S: Final = 4.0
#: Outdoors there is nothing for sign memory to do -- the phone's Maps engine
#: owns navigation out there -- but the sweep still has to notice the wearer
#: walking back inside, so it slows down instead of stopping.
OUTDOOR_INTERVAL_MULTIPLIER: Final = 5

#: The model's own word for how sure it is, as a number memory can rank.
CONFIDENCE_SCORES: Final = {"low": 0.3, "medium": 0.6, "high": 0.9}
#: Only these are stored. "low" is the model telling us it is guessing.
TRUSTED_CONFIDENCES: Final = frozenset({"medium", "high"})

#: A sweep's views, fetched fresh each time. A plain awaitable rather than a
#: Protocol: the only real implementation is a bound method on the scene
#: service, and a closure is what the tests want.
ViewSource = Callable[[], Awaitable[Sequence["View"]]]


def scanning_enabled(environ: dict[str, str] | None = None) -> bool:
    """Whether the sweep should run at all (``CANE_SIGN_SCAN=off`` disables)."""
    env = os.environ if environ is None else environ
    return env.get(SIGN_SCAN_ENV, "on").strip().lower() not in {"off", "0", "false", "no"}


def scan_interval_s(environ: dict[str, str] | None = None) -> float:
    """Seconds between sweeps, from the environment or the default."""
    env = os.environ if environ is None else environ
    raw = env.get(SIGN_SCAN_INTERVAL_ENV)
    if not raw:
        return DEFAULT_SCAN_INTERVAL_S
    try:
        return max(0.5, float(raw))
    except ValueError:
        logger.warning(
            "%s=%r is not a number; using %.1fs",
            SIGN_SCAN_INTERVAL_ENV,
            raw,
            DEFAULT_SCAN_INTERVAL_S,
        )
        return DEFAULT_SCAN_INTERVAL_S


def setting_from(reply: ScanReply) -> bool | None:
    """``True`` indoors, ``False`` outdoors, ``None`` when the model did not say.

    Three states on purpose. "I do not know" must not read as "outdoors", or
    one unparseable sweep would hand navigation to a Maps engine that has no
    GPS fix to work with.
    """
    said = (reply.setting or "").strip().lower()
    if said == "indoor":
        return True
    if said == "outdoor":
        return False
    return None


def sightings_from(reply: ScanReply, views: Sequence[View]) -> tuple[Sighting, ...]:
    """The signs in one sweep that are worth remembering, with their bearings.

    The bearing comes from ``View.bearing_at`` -- the same geometry the
    detector events and the query path use -- so a sign's direction is
    computed from the crop it was seen in and never echoed back as a word
    from the model.
    """
    if not reply.signs:
        return ()
    kept: list[Sighting] = []
    for sign in reply.signs[:MAX_SIGNS]:
        sighting = _sighting_from(sign, views)
        if sighting is not None:
            kept.append(sighting)
    return tuple(kept)


def _sighting_from(sign: object, views: Sequence[View]) -> Sighting | None:
    """One reading, or ``None`` if any part of it cannot be trusted."""
    text = (getattr(sign, "text", None) or "").strip()
    if not text or not normalize(text):
        return None

    confidence = (getattr(sign, "confidence", None) or "").strip().lower()
    if confidence not in TRUSTED_CONFIDENCES:
        return None

    index = getattr(sign, "image_index", None)
    x_center = getattr(sign, "x_center", None)
    if index is None or x_center is None:
        return None
    if not 0 <= index < len(views):
        return None

    return Sighting.seen(
        text=text,
        bearing_deg=views[index].bearing_at(float(x_center)),
        confidence=CONFIDENCE_SCORES[confidence],
    )


class SignScanner:
    """One sweep every few seconds, for the life of the walk.

    Owns no frames and no sockets: it is handed a way to get the current views
    and a memory to publish into, so the whole thing is testable against a
    scripted model.
    """

    def __init__(
        self,
        *,
        vlm: SignScanningModel,
        views: ViewSource,
        memory: PlaceMemory,
        interval_s: float | None = None,
        busy: Callable[[], bool] | None = None,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        self._vlm = vlm
        self._views = views
        self._memory = memory
        self._interval_s = DEFAULT_SCAN_INTERVAL_S if interval_s is None else interval_s
        self._busy = busy if busy is not None else _never_busy
        self._monotonic = monotonic
        self._indoor: bool | None = None
        self._sweeps = 0

    @property
    def indoor(self) -> bool | None:
        """The last sweep's verdict, or ``None`` before the first one.

        ``None`` is a real answer: it means the laptop has not seen enough to
        claim either way, and nothing downstream should switch modes on it.
        """
        return self._indoor

    async def run(self) -> None:
        """Sweep forever. Cancelled by whoever owns the task."""
        logger.info(
            "sign scan every %.1fs with %s", self._interval_s, getattr(self._vlm, "name", "model")
        )
        while True:
            await self.scan_once()
            await asyncio.sleep(self._sleep_s())

    async def scan_once(self) -> None:
        """One sweep. Never raises except to be cancelled."""
        if self._busy():
            return

        views = await self._current_views()
        if not views:
            return

        started = self._monotonic()
        try:
            reply = await self._vlm.scan(build_scan_prompt(), [view.image for view in views])
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - a failed sweep is the next sweep's problem
            logger.warning("sign scan failed: %s", exc)
            return

        self._sweeps += 1
        setting = setting_from(reply)
        if setting is not None:
            self._note_setting(setting)
        if self._indoor is False:
            return

        found = sightings_from(reply, views)
        for sighting in found:
            self._memory.publish(sighting)
        if found:
            logger.info(
                "sign scan %d: %s in %.1fs",
                self._sweeps,
                ", ".join(f"{s.text}@{s.bearing_deg:g}" for s in found),
                self._monotonic() - started,
            )

    async def _current_views(self) -> Sequence[View]:
        """The views for this sweep, or nothing if the camera has no frame."""
        try:
            return await self._views()
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - no frame is a skipped sweep, not a crash
            logger.warning("sign scan could not get a frame: %s", exc)
            return ()

    def _note_setting(self, indoor: bool) -> None:
        if indoor is not self._indoor:
            logger.info("sign scan sees %s", "indoors" if indoor else "outdoors")
        self._indoor = indoor

    def _sleep_s(self) -> float:
        """Sweep rarely once outdoors; there is nothing to remember out there."""
        if self._indoor is False:
            return self._interval_s * OUTDOOR_INTERVAL_MULTIPLIER
        return self._interval_s


def _never_busy() -> bool:
    return False
