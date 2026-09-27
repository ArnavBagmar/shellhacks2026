"""One ``query`` in, one ``answer`` out.

Order of work for a query:

1. settle -- the phone has just told the wearer "hold still"; wait
   ``settle_s`` so the frame is not smeared by the turn they made to speak;
2. the freshest X4 equirect -> the bridge's six 60-degree crops (never the
   raw equirect, build-plan F11) + the phone's photo when it sent one;
3. one VLM call (the primary model, then a lighter fallback if it errors or
   overruns), bounded overall by ``vlm_timeout_s``;
4. the reply is checked and mapped to a contract-valid ``SceneAnswer`` --
   image index + x position -> bearing with the detector-event geometry.

A newer query supersedes an older one still in flight (the phone has
already moved on). Every failure -- no frame, no key, timeout, junk JSON --
is ``status: unavailable``, never an exception and never silence.
"""

from __future__ import annotations

import asyncio
import base64
import binascii
import json
import logging
import os
import time
from dataclasses import dataclass, replace
from datetime import datetime
from pathlib import Path
from typing import Callable, Sequence, get_args

import cv2
import numpy as np

from cane.bridge.dewarp import CROP_FOV_DEG, RING_CROP_COUNT, dewarp_equirect, encode_jpeg
from cane.bridge.events import normalize_bearing_deg
from cane.scene.debug_view import DebugHub
from cane.scene.tall_views import YAWS_DEG as TALL_YAWS_DEG
from cane.scene.tall_views import HEAD_PITCH_DEG, HEAD_VFOV_DEG, bearing_offset, tall_views, x_for_offset
from cane.scene.frames import EquirectProvider
from cane.scene.gap import ClearWay, ClearWayProvider
from cane.scene.gap_speech import clear_way_fragment, clear_way_sentence
from cane.scene.place_names import spoken_subject
from cane.scene.places import PlaceProvider, Recall, Sighting
from cane.scene.prompt import GapHint, build_prompt, crop_label, phone_label
from cane.scene.vlm import LabeledImage, VisionLanguageModel, VlmReply
from cane.scene.weather import WeatherSource, is_weather_question
from cane.transport.messages import Confidence, PhoneBox, SceneAnswer, SceneQuery

logger = logging.getLogger(__name__)

DEFAULT_SETTLE_S = 0.4
DEFAULT_VLM_TIMEOUT_S = 15.0
DEFAULT_CROP_PX = 512
DEFAULT_JPEG_QUALITY = 75
MAX_SUBJECT_CHARS = 80
MAX_DETAIL_CHARS = 240
#: Gemini's box_2d scale.
BOX_SCALE = 1000.0
#: Where the wearer is, for ``ask`` questions ("how's the weather?"), e.g.
#: "FIU Graham Center, Miami, Florida". Optional.
LOCATION_ENV = "CANE_LOCATION"
#: describe_front without a phone photo falls back to the three forward crops.
FRONT_CROP_INDICES = (5, 0, 1)
#: An x of exactly 0 or 1 is a legitimate edge, and inverting a bearing that
#: was rounded to a tenth of a degree can land a hair outside it. A pixel's
#: worth of slack keeps the edge columns of a crop usable instead of silently
#: declaring the ring's own boundary off-image.
X_EDGE_TOLERANCE = 1e-6
#: How a ``found`` answer carries the ``blocked`` message: the phone inlines a
#: found answer's detail after a comma, so the safety fragment leads and the
#: model's route hint follows it.
FOUND_DETAIL_JOIN = "; "
_CONFIDENCES: tuple[str, ...] = get_args(Confidence)
#: The sweep is not a question, but ``_crop_views`` is written against one.
#: ``describe_around`` is the kind that narrows nothing, so this asks for the
#: whole ring -- which is the point of scanning at all.
_SCAN_QUERY = SceneQuery(
    query_id="sign-scan",
    kind="describe_around",
    target=None,
    utterance="",
    t_phone_ms=0,
    phone_frame=None,
)


@dataclass(frozen=True)
class ViewLayout:
    """How the 360 frame is cut for the model, and where the phone photo goes."""

    #: Negative tilts the views down. 0 with ``vfov_deg`` 60 is the bridge's
    #: own square crops.
    pitch_deg: float = 0.0
    vfov_deg: float = CROP_FOV_DEG
    #: Live 2026-09-26 the model found a Rubik's cube in the phone photo (sent
    #: last, image 6) but answered image_index 0; the box it drew was the
    #: cube's place in the photo. The phone photo is now image 0.
    phone_first: bool = False
    #: Print "IMAGE n" on each image. Replaying a live question (2026-09-26,
    #: a pink Monster can in image 6): unstamped, Gemini answered image 5 in
    #: 4 of 4 runs with the box drawn where the can is in image 6; stamped,
    #: image 6 in 4 of 4. It reads the number off the picture it found the
    #: thing in instead of counting images.
    number_stamps: bool = False

    @property
    def level(self) -> bool:
        return self.pitch_deg == 0.0 and self.vfov_deg == CROP_FOV_DEG


#: The bridge's crops, phone photo last (what the tests pin down).
LEVEL_LAYOUT = ViewLayout()
#: The X4 worn on the head: tables and the floor ahead are 30-70 degrees
#: below eye level, out of a level 60-degree crop.
HEAD_WORN_LAYOUT = ViewLayout(
    pitch_deg=HEAD_PITCH_DEG, vfov_deg=HEAD_VFOV_DEG, phone_first=True, number_stamps=True
)
STAMP_NOTE = (
    " Each image has its number printed in its top-left corner (IMAGE n): image_index must be "
    "the number printed on the image the thing is in."
)


@dataclass(frozen=True)
class View:
    """One image sent to the model and how to turn a position in it into a
    torso-relative bearing: ``centre_deg + (x - 0.5) * fov_deg`` for a level
    view, exact pinhole geometry for a tilted one (``tall_views``).
    """

    image: LabeledImage
    centre_deg: float
    fov_deg: float
    #: The phone photo: the only image with depth behind it.
    is_phone: bool = False
    pitch_deg: float = 0.0
    vfov_deg: float | None = None

    @property
    def tilted(self) -> bool:
        return self.vfov_deg is not None and (self.pitch_deg != 0.0 or self.vfov_deg != self.fov_deg)

    def bearing_at(self, x_center: float, y_center: float = 0.5) -> float:
        x = min(max(x_center, 0.0), 1.0)
        if self.tilted:
            offset = bearing_offset(
                x, min(max(y_center, 0.0), 1.0),
                hfov_deg=self.fov_deg, vfov_deg=self.vfov_deg or self.fov_deg, pitch_deg=self.pitch_deg,
            )
            return round(normalize_bearing_deg(self.centre_deg + offset), 1)
        # A tenth of a degree is far finer than a VLM's x estimate; rounding
        # just keeps the wire and the logs readable.
        return round(normalize_bearing_deg(self.centre_deg + (x - 0.5) * self.fov_deg), 1)

    def x_for_bearing(self, bearing_deg: float) -> float | None:
        """:meth:`bearing_at` run backwards, or ``None`` if this view cannot
        see that bearing at all.

        Step 26 points the model at the depth ring's gap. Doing it by
        inverting the *same* expression is what guarantees the model is looking
        where our own geometry says the gap is, and ``normalize_bearing_deg``
        on the difference is what makes a rear view's seam behave (a bearing
        of -170 is 10 degrees inside a view centred on 180).
        """
        offset = normalize_bearing_deg(bearing_deg - self.centre_deg)
        if self.tilted:
            tilted_x = x_for_offset(offset, hfov_deg=self.fov_deg, pitch_deg=self.pitch_deg)
            if tilted_x is None:
                return None
            x = tilted_x
        else:
            x = 0.5 + offset / self.fov_deg
        if -X_EDGE_TOLERANCE <= x <= 1.0 + X_EDGE_TOLERANCE:
            return min(1.0, max(0.0, x))
        return None


def gap_hint_for(way: ClearWay | None, views: Sequence[View]) -> GapHint | None:
    """Which image, and where in it, the depth ring's clearest way falls.

    The most *central* view wins when several can see the gap: an x near an
    edge is the one a model is least able to use, and a crop's centre is where
    the dewarp is least stretched.
    """
    if way is None:
        return None
    best: tuple[int, float] | None = None
    for index, view in enumerate(views):
        x = view.x_for_bearing(way.bearing_deg)
        if x is None:
            continue
        if best is None or abs(x - 0.5) < abs(best[1] - 0.5):
            best = (index, x)
    if best is None:
        return GapHint(image_index=None, x_center=None, blocked=way.blocked)
    return GapHint(
        image_index=best[0], x_center=round(best[1], 2), blocked=way.blocked
    )


class SceneQueryService:
    def __init__(
        self,
        vlm: VisionLanguageModel,
        frames: EquirectProvider | None,
        *,
        clear_ways: ClearWayProvider | None = None,
        delta_deg: float = 0.0,
        settle_s: float = DEFAULT_SETTLE_S,
        vlm_timeout_s: float = DEFAULT_VLM_TIMEOUT_S,
        crop_px: int = DEFAULT_CROP_PX,
        jpeg_quality: int = DEFAULT_JPEG_QUALITY,
        monotonic: Callable[[], float] = time.monotonic,
        dump_dir: Path | None = None,
        location: str | None = None,
        now: Callable[[], datetime] = datetime.now,
        weather: WeatherSource | None = None,
        debug: DebugHub | None = None,
        layout: ViewLayout = LEVEL_LAYOUT,
        places: PlaceProvider | None = None,
    ) -> None:
        self._places = places
        self._layout = layout
        self._weather = weather
        self._debug = debug
        self._dump_dir = dump_dir
        self._location = location if location is not None else (os.environ.get(LOCATION_ENV, "").strip() or None)
        self._now = now
        self._vlm = vlm
        self._frames = frames
        self._clear_ways = clear_ways
        self._delta_deg = delta_deg
        self._settle_s = settle_s
        self._vlm_timeout_s = vlm_timeout_s
        self._crop_px = crop_px
        self._jpeg_quality = jpeg_quality
        self._monotonic = monotonic
        self._current: asyncio.Task[object] | None = None

    async def handle(self, query: SceneQuery) -> SceneAnswer | None:
        """Answer ``query``, or return ``None`` if a newer query superseded
        it while it was being worked on.
        """
        previous = self._current
        me = asyncio.current_task()
        self._current = me
        if previous is not None and previous is not me and not previous.done():
            logger.info("query %s supersedes the one still in flight", query.query_id)
            previous.cancel()
        try:
            return await self.answer(query)
        except asyncio.CancelledError:
            if self._current is not me:
                return None
            raise
        finally:
            if self._current is me:
                self._current = None

    async def answer(self, query: SceneQuery) -> SceneAnswer:
        started = self._monotonic()
        # Memory first, but only when it can still point. A sighting fresh
        # enough to carry a bearing is a complete answer and the scanner has
        # already paid for it, so asking the model again would cost the wearer
        # seconds for something already known. A sighting too old to point
        # waits below: the sign may be in view right now.
        remembered = self._recall(query)
        if remembered is not None and remembered.bearing_deg is not None:
            return self._remembered(query, remembered, started)
        # Looked up while the wearer holds still and the crops are cut.
        facts_task = None
        if query.kind == "ask" and self._weather is not None and is_weather_question(query.utterance):
            facts_task = asyncio.create_task(self._weather.summary())
        try:
            views = await self._views(query)
            facts = await facts_task if facts_task is not None else None
        finally:
            if facts_task is not None and not facts_task.done():
                facts_task.cancel()
        if not views:
            logger.warning("query %s: no X4 frame and no phone photo", query.query_id)
            return await self._debugged(query, views, None, self._unavailable(query, started), "no images")
        # Read once: the fact in the prompt and the fact the wearer is told
        # must be the same tick, or the two halves of this step could disagree
        # across a 1-3 s model call.
        way = None if self._clear_ways is None else self._clear_ways.latest_clear_way()
        prompt = self._prompt(query, facts, gap_hint_for(way, views))
        try:
            reply = await asyncio.wait_for(
                self._vlm.ask(
                    prompt,
                    [v.image for v in views],
                    search=query.kind == "ask" and facts is None,
                ),
                timeout=self._vlm_timeout_s,
            )
        except TimeoutError:
            logger.warning("query %s: %s timed out after %.1fs", query.query_id, self._vlm.name, self._vlm_timeout_s)
            return await self._debugged(query, views, None, self._unavailable(query, started), "Gemini timed out")
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - a broken VLM is "unavailable", not a crash
            logger.warning("query %s: %s failed: %s", query.query_id, self._vlm.name, exc)
            return await self._debugged(query, views, None, self._unavailable(query, started), str(exc)[:200])
        answer = self._to_answer(query, reply, views, started, way)
        # The ring looked and saw nothing, but the wearer walked past one
        # earlier. "I don't see any" would be true and useless; "you passed
        # one, a while ago" is the answer they asked for.
        if answer.status == "not_found" and remembered is not None:
            answer = self._remembered(query, remembered, started)
        if self._dump_dir is not None:
            await asyncio.to_thread(self._dump, query, views, reply, answer, prompt)
        await self._debugged(query, views, reply, answer, None, facts)
        index = reply.image_index
        where = "phone" if index is not None and 0 <= index < len(views) and views[index].is_phone else index
        logger.info(
            "query %s -> %s %r bearing=%s image=%s box=%s in %d ms",
            query.query_id, answer.status, answer.subject or answer.detail,
            answer.bearing_deg, where, "yes" if answer.phone_box else "no", answer.latency_ms,
        )
        return answer

    async def _debugged(
        self, query: SceneQuery, views: list[View], reply: VlmReply | None, answer: SceneAnswer,
        error: str | None, facts: str | None = None,
    ) -> SceneAnswer:
        """Hand the question to the debug page (off the loop), then pass the answer on."""
        if self._debug is not None:
            await asyncio.to_thread(self._debug.record_query, query, views, reply, answer, error=error, facts=facts)
        return answer

    def _prompt(
        self, query: SceneQuery, facts: str | None = None, gap: GapHint | None = None
    ) -> str:
        prompt = build_prompt(
            query, gap, now=self._now(), location=self._location, facts=facts
        )
        return prompt + STAMP_NOTE if self._layout.number_stamps else prompt

    # ------------------------------------------------------------------ views

    async def _views(self, query: SceneQuery) -> list[View]:
        await asyncio.sleep(self._settle_s)
        views: list[View] = []
        equirect = None if self._frames is None else await self._frames.fresh_equirect()
        if equirect is not None:
            try:
                views.extend(await asyncio.to_thread(self._crop_views, equirect, query))
            except ValueError as exc:
                logger.warning("query %s: X4 frame unusable: %s", query.query_id, exc)
        if self._layout.phone_first:
            phone = self._phone_view(query, index=0)
            if phone is not None:
                views = [phone, *(self._relabel(v, i + 1) for i, v in enumerate(views))]
        else:
            phone = self._phone_view(query, index=len(views))
            if phone is not None:
                views.append(phone)
        if self._layout.number_stamps:
            views = await asyncio.to_thread(lambda: [_stamped(v, i) for i, v in enumerate(views)])
        return views

    def _relabel(self, view: View, index: int) -> View:
        """The same crop, numbered ``index`` (the phone photo went first)."""
        label = crop_label(index, view.centre_deg - self._delta_deg, tall=view.tilted)
        return replace(view, image=LabeledImage(label, view.image.jpeg))

    def _crop_views(self, equirect: object, query: SceneQuery) -> list[View]:
        layout = self._layout
        if layout.level:
            cropset = dewarp_equirect(equirect, self._crop_px, t_arrival_laptop_ms=0)  # type: ignore[arg-type]
            # The six ring crops only. The nadir crop C6 has a radial bearing
            # geometry that ``View.centre_deg``/``fov_deg`` cannot express; giving
            # it to the model is milestone 3 of the x4-sphere-coverage PRD.
            images = cropset.images[:RING_CROP_COUNT]
            yaws = cropset.yaws_deg[:RING_CROP_COUNT]
        else:
            images = tuple(tall_views(equirect, self._crop_px, vfov_deg=layout.vfov_deg,  # type: ignore[arg-type]
                                      pitch_deg=layout.pitch_deg))
            yaws = TALL_YAWS_DEG
        indices: Sequence[int] = range(len(images))
        if query.kind == "describe_front" and query.phone_frame is not None:
            indices = ()
        elif query.kind == "describe_front":
            indices = FRONT_CROP_INDICES
        views = []
        for position, crop_index in enumerate(indices):
            yaw = yaws[crop_index]
            jpeg = encode_jpeg(images[crop_index], self._jpeg_quality)
            views.append(
                View(
                    image=LabeledImage(crop_label(position, yaw, tall=not layout.level), jpeg),
                    centre_deg=yaw + self._delta_deg,
                    fov_deg=CROP_FOV_DEG,
                    pitch_deg=layout.pitch_deg,
                    vfov_deg=None if layout.level else layout.vfov_deg,
                )
            )
        return views

    @staticmethod
    def _phone_view(query: SceneQuery, index: int) -> View | None:
        frame = query.phone_frame
        if frame is None:
            return None
        try:
            jpeg = base64.b64decode(frame.jpeg_b64, validate=True)
        except (binascii.Error, ValueError):
            logger.warning("query %s: phone photo is not valid base64; ignoring it", query.query_id)
            return None
        return View(
            image=LabeledImage(phone_label(index, frame.hfov_deg), jpeg),
            centre_deg=0.0,
            fov_deg=frame.hfov_deg,
            is_phone=True,
        )

    def _dump(
        self,
        query: SceneQuery,
        views: list[View],
        reply: VlmReply,
        answer: SceneAnswer,
        prompt: str,
    ) -> None:
        """Debug: exactly what the model saw and said, one folder per query."""
        folder = self._dump_dir / query.query_id  # type: ignore[operator]
        try:
            folder.mkdir(parents=True, exist_ok=True)
            for index, view in enumerate(views):
                (folder / f"image-{index}.jpg").write_bytes(view.image.jpeg)
            summary = {
                "query": query.model_dump(exclude={"phone_frame"}),
                "labels": [v.image.label for v in views],
                # The prompt that was actually sent, passed in rather than
                # rebuilt: it depends on a ring reading, and on looked-up
                # facts, that have both moved on by now.
                "prompt": prompt,
                "model_reply": reply.model_dump(),
                "answer": answer.model_dump(),
            }
            (folder / "query.json").write_text(json.dumps(summary, indent=2))
            logger.info("query %s: views saved to %s", query.query_id, folder)
        except OSError as exc:
            logger.warning("could not save views to %s: %s", folder, exc)

    # ---------------------------------------------------------------- mapping

    def _to_answer(
        self,
        query: SceneQuery,
        reply: VlmReply,
        views: list[View],
        started: float,
        way: ClearWay | None = None,
    ) -> SceneAnswer:
        status = (reply.status or "").strip().lower()
        detail = _clean(reply.detail, MAX_DETAIL_CHARS)
        if query.kind == "ask":
            located = (
                status == "found" and reply.image_index is not None
                and (reply.x_center is not None or _box(reply.box_2d) is not None)
                and 0 <= reply.image_index < len(views) and _clean(reply.subject, MAX_SUBJECT_CHARS)
            )
            if located:
                return self._found(query, reply, views, started, detail, way)
            spoken = detail or _clean(reply.subject, MAX_DETAIL_CHARS)
            if spoken is None:
                return self._unavailable(query, started)
            return self._build(query, started, status="described", detail=spoken)
        if query.kind != "find":
            if detail is None:
                # A ring is not a description. Dressing one up as "described"
                # because the model failed would tell the wearer their question
                # was answered when it was not; "unavailable" is the truth.
                return self._unavailable(query, started)
            return self._build(
                query, started, status="described", detail=_described_detail(way, detail)
            )
        if status != "found":
            return self._build(query, started, status="not_found")
        return self._found(query, reply, views, started, detail, way)

    def _found(
        self,
        query: SceneQuery,
        reply: VlmReply,
        views: list[View],
        started: float,
        detail: str | None,
        way: ClearWay | None = None,
    ) -> SceneAnswer:
        bearing = None
        phone_box = None
        index = reply.image_index
        box = _box(reply.box_2d)
        if index is not None and 0 <= index < len(views):
            # The box first: it is Gemini's native format and was right every
            # time live, while x_center sometimes came back on the box's
            # 0-1000 scale (221 for 0.221) and clamped to the right edge --
            # "everything is to the right" (2026-09-26).
            x_center = (box.x_min + box.x_max) / 2 if box is not None else _fraction(reply.x_center)
            y_center = (box.y_min + box.y_max) / 2 if box is not None else 0.5
            if x_center is not None:
                bearing = views[index].bearing_at(x_center, y_center)
            if views[index].is_phone:
                phone_box = box
        confidence = (reply.confidence or "").strip().lower()
        return self._build(
            query,
            started,
            status="found",
            subject=_clean(reply.subject, MAX_SUBJECT_CHARS) or query.target,
            bearing_deg=bearing,
            detail=_found_detail(way, detail),
            confidence=confidence if confidence in _CONFIDENCES else None,
            phone_box=phone_box,
        )

    @property
    def busy(self) -> bool:
        """Whether one of the wearer's questions is in flight right now.

        The background sign sweep checks this before spending a model call:
        the wearer is standing still waiting for an answer, and a sweep that
        nobody asked for must not be what they are queued behind.
        """
        return self._current is not None

    async def scan_views(self) -> list[View]:
        """The current ring crops, for the background sign sweep.

        Deliberately not ``_views``: there is no settle wait because nobody
        just turned their head to speak, and no phone photo because the phone
        only sends one with a question.
        """
        if self._frames is None:
            return []
        equirect = await self._frames.fresh_equirect()
        if equirect is None:
            return []
        try:
            return await asyncio.to_thread(self._crop_views, equirect, _SCAN_QUERY)
        except ValueError as exc:
            logger.warning("sign scan: X4 frame unusable: %s", exc)
            return []

    def _recall(self, query: SceneQuery) -> Recall | None:
        """What memory holds for this question, if it is a ``find`` at all."""
        if self._places is None or query.kind != "find" or not query.target:
            return None
        return self._places.recall(query.target)

    def _remembered(self, query: SceneQuery, recalled: Recall, started: float) -> SceneAnswer:
        """An answer built from a sign seen earlier rather than one in view.

        ``bearing_deg`` is whatever memory was willing to stand behind: the
        angle when the sighting is fresh, ``None`` once it is not, which the
        phone already speaks as "...nearby, direction unclear". Confidence
        drops to ``low`` in that case so the wearer hears "Maybe" -- the
        honest word for a place we know exists but can no longer point at.
        """
        sighting = recalled.sighting
        return self._build(
            query,
            started,
            status="found",
            subject=_clean(spoken_subject(sighting.text), MAX_SUBJECT_CHARS) or query.target,
            bearing_deg=recalled.bearing_deg,
            detail=_age_clause(recalled.age_ms),
            confidence="low" if recalled.bearing_deg is None else _memory_confidence(sighting),
        )

    def _unavailable(self, query: SceneQuery, started: float) -> SceneAnswer:
        return self._build(query, started, status="unavailable")

    def _build(self, query: SceneQuery, started: float, **fields: object) -> SceneAnswer:
        latency_ms = max(0, round((self._monotonic() - started) * 1000))
        values: dict[str, object] = {
            "query_id": query.query_id,
            "subject": None,
            "bearing_deg": None,
            "detail": None,
            "confidence": None,
            "latency_ms": latency_ms,
        }
        values.update(fields)
        return SceneAnswer.model_validate(values)


#: Under this, a sighting is "now" and saying when it was seen is noise.
MEMORY_FRESH_MS = 5_000
#: A sign read this many times is solid enough to speak with confidence.
MEMORY_CONFIDENT_SIGHTINGS = 2


def _age_clause(age_ms: int) -> str | None:
    """"seen 15 seconds ago", or nothing at all if it was seen just now.

    The wearer needs this to judge the answer for themselves: a restroom sign
    seen four minutes ago is worth walking toward, and one seen four seconds
    ago is worth turning toward. Only they can tell the difference, so only
    they can be told.
    """
    if age_ms < MEMORY_FRESH_MS:
        return None
    seconds = round(age_ms / 1000)
    if seconds < 60:
        return f"seen {seconds} seconds ago"
    minutes = round(seconds / 60)
    return f"seen about {minutes} minute{'s' if minutes != 1 else ''} ago"


def _memory_confidence(sighting: Sighting) -> str:
    """How sure a *pointable* memory is: the model's own word, firmed up by
    having read the same sign more than once."""
    if sighting.confidence >= 0.9 and sighting.seen_count >= MEMORY_CONFIDENT_SIGHTINGS:
        return "high"
    return "medium"


def _described_detail(way: ClearWay | None, detail: str) -> str:
    """A description, led by what the depth ring found (step 26 A and B).

    The ring's sentence comes **first** and the model's prose is what the
    240-character contract cap eats into, so the one thing the wearer cannot
    learn anywhere else is the one thing that cannot be truncated away.
    """
    if way is None:
        return detail
    return f"{clear_way_sentence(way)} {detail}"[:MAX_DETAIL_CHARS]


def _found_detail(way: ClearWay | None, detail: str | None) -> str | None:
    """A found answer's hint, with the ``blocked`` message if there is one.

    Only when ``blocked``. A found answer already carries a direction in
    ``bearing_deg``, and adding "most open to your right" beside "to your
    right, restroom sign" puts two unrelated directions in one breath --
    which is the confusion this step exists to prevent, wearing a different
    hat. ``blocked`` is the exception because plan Sec 3.2 ruling 4 leaves it
    nowhere else to go.
    """
    if way is None or not way.blocked:
        return detail
    fragment = clear_way_fragment(way)
    joined = fragment if detail is None else fragment + FOUND_DETAIL_JOIN + detail
    return joined[:MAX_DETAIL_CHARS]

def _stamped(view: View, index: int) -> View:
    """``view`` with "IMAGE n" printed on a thin bar across its top. The bar
    covers pixels but keeps the size, so box coordinates are unchanged.
    """
    image = cv2.imdecode(np.frombuffer(view.image.jpeg, np.uint8), cv2.IMREAD_COLOR)
    if image is None:
        return view
    w = image.shape[1]
    scale = max(0.5, w / 420)
    bar = int(30 * scale)
    cv2.rectangle(image, (0, 0), (w, bar), (0, 0, 0), -1)
    cv2.putText(image, f"IMAGE {index}", (int(8 * scale), int(22 * scale)), cv2.FONT_HERSHEY_SIMPLEX,
                0.7 * scale, (255, 255, 255), max(1, int(2 * scale)), cv2.LINE_AA)
    ok, buf = cv2.imencode(".jpg", image, [cv2.IMWRITE_JPEG_QUALITY, 85])
    return replace(view, image=LabeledImage(view.image.label, buf.tobytes())) if ok else view


def _fraction(x: float | None) -> float | None:
    """x_center as 0-1, accepting the 0-1000 scale Gemini sometimes uses."""
    if x is None or x != x:
        return None
    return x / BOX_SCALE if 1.0 < x <= BOX_SCALE else x


def _box(raw: Sequence[float] | None) -> PhoneBox | None:
    """Gemini's [ymin, xmin, ymax, xmax] on 0-1000 -> fractions, or None
    for anything that is not a usable box (wrong length, zero size).
    Swapped corners are put right; values past the edges are clamped.
    """
    if raw is None or len(raw) != 4:
        return None
    try:
        values = [min(max(float(v) / BOX_SCALE, 0.0), 1.0) for v in raw]
    except (TypeError, ValueError):
        return None
    if any(v != v for v in values):  # NaN
        return None
    y0, x0, y1, x1 = values
    x_min, x_max = sorted((x0, x1))
    y_min, y_max = sorted((y0, y1))
    if x_max - x_min < 0.005 or y_max - y_min < 0.005:
        return None
    return PhoneBox(
        x_min=round(x_min, 3), y_min=round(y_min, 3), x_max=round(x_max, 3), y_max=round(y_max, 3)
    )


def _clean(text: str | None, max_chars: int) -> str | None:
    if text is None:
        return None
    text = " ".join(text.split())
    if not text or text.lower() in {"null", "none", "n/a"}:
        return None
    return text[:max_chars]
