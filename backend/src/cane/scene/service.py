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
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Sequence, get_args

from cane.bridge.dewarp import CROP_FOV_DEG, dewarp_equirect, encode_jpeg
from cane.bridge.events import normalize_bearing_deg
from cane.scene.frames import EquirectProvider
from cane.scene.gap import ClearWay, ClearWayProvider
from cane.scene.gap_speech import clear_way_fragment, clear_way_sentence
from cane.scene.prompt import GapHint, build_prompt, crop_label, phone_label
from cane.scene.vlm import LabeledImage, VisionLanguageModel, VlmReply
from cane.transport.messages import Confidence, SceneAnswer, SceneQuery

logger = logging.getLogger(__name__)

DEFAULT_SETTLE_S = 0.4
DEFAULT_VLM_TIMEOUT_S = 15.0
DEFAULT_CROP_PX = 512
DEFAULT_JPEG_QUALITY = 75
MAX_SUBJECT_CHARS = 80
MAX_DETAIL_CHARS = 240
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


@dataclass(frozen=True)
class View:
    """One image sent to the model and how to turn a position in it into a
    torso-relative bearing: ``centre_deg + (x - 0.5) * fov_deg``.
    """

    image: LabeledImage
    centre_deg: float
    fov_deg: float

    def bearing_at(self, x_center: float) -> float:
        x = min(max(x_center, 0.0), 1.0)
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
    ) -> None:
        self._dump_dir = dump_dir
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
        views = await self._views(query)
        if not views:
            logger.warning("query %s: no X4 frame and no phone photo", query.query_id)
            return self._unavailable(query, started)
        # Read once: the fact in the prompt and the fact the wearer is told
        # must be the same tick, or the two halves of this step could disagree
        # across a 1-3 s model call.
        way = None if self._clear_ways is None else self._clear_ways.latest_clear_way()
        prompt = build_prompt(query, gap_hint_for(way, views))
        try:
            reply = await asyncio.wait_for(
                self._vlm.ask(prompt, [v.image for v in views]),
                timeout=self._vlm_timeout_s,
            )
        except TimeoutError:
            logger.warning("query %s: %s timed out after %.1fs", query.query_id, self._vlm.name, self._vlm_timeout_s)
            return self._unavailable(query, started)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - a broken VLM is "unavailable", not a crash
            logger.warning("query %s: %s failed: %s", query.query_id, self._vlm.name, exc)
            return self._unavailable(query, started)
        answer = self._to_answer(query, reply, views, started, way)
        if self._dump_dir is not None:
            await asyncio.to_thread(self._dump, query, views, reply, answer, prompt)
        logger.info(
            "query %s -> %s %r bearing=%s in %d ms",
            query.query_id, answer.status, answer.subject or answer.detail,
            answer.bearing_deg, answer.latency_ms,
        )
        return answer

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
        phone = self._phone_view(query, index=len(views))
        if phone is not None:
            views.append(phone)
        return views

    def _crop_views(self, equirect: object, query: SceneQuery) -> list[View]:
        cropset = dewarp_equirect(equirect, self._crop_px, t_arrival_laptop_ms=0)  # type: ignore[arg-type]
        indices: Sequence[int] = range(len(cropset.images))
        if query.kind == "describe_front" and query.phone_frame is not None:
            indices = ()
        elif query.kind == "describe_front":
            indices = FRONT_CROP_INDICES
        views = []
        for position, crop_index in enumerate(indices):
            yaw = cropset.yaws_deg[crop_index]
            jpeg = encode_jpeg(cropset.images[crop_index], self._jpeg_quality)
            views.append(
                View(
                    image=LabeledImage(crop_label(position, yaw), jpeg),
                    centre_deg=yaw + self._delta_deg,
                    fov_deg=CROP_FOV_DEG,
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
                # rebuilt: it now depends on a ring reading that has moved on.
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
        bearing = None
        index = reply.image_index
        if index is not None and 0 <= index < len(views) and reply.x_center is not None:
            bearing = views[index].bearing_at(reply.x_center)
        confidence = (reply.confidence or "").strip().lower()
        return self._build(
            query,
            started,
            status="found",
            subject=_clean(reply.subject, MAX_SUBJECT_CHARS) or query.target,
            bearing_deg=bearing,
            detail=_found_detail(way, detail),
            confidence=confidence if confidence in _CONFIDENCES else None,
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


def _clean(text: str | None, max_chars: int) -> str | None:
    if text is None:
        return None
    text = " ".join(text.split())
    if not text or text.lower() in {"null", "none", "n/a"}:
        return None
    return text[:max_chars]
