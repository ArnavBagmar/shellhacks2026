"""The vision-language model behind one interface: Gemini for real, a fake
for tests.

The reply is deliberately loose (plain strings, optional everything): a
VLM's JSON is untrusted input, and ``service.py`` is where it is checked
and turned into a contract-valid ``SceneAnswer``.
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
from dataclasses import dataclass
from typing import Protocol, Sequence, TypeVar

from pydantic import BaseModel, ConfigDict

logger = logging.getLogger(__name__)

#: build-plan.md Sec 1.2 -- every PDF-era model ID is dead. Override with
#: CANE_GEMINI_MODEL if Google renames it again.
#: Measured live 2026-09-26 on the repo's still equirect: flash-lite answered
#: in 1.1 s and found the doorway; 3.8-flash took 5.7 s (6.0 s just to warm)
#: and found nothing. The wearer is standing still waiting, so lite leads.
DEFAULT_GEMINI_MODEL = "gemini-3.5-flash-lite"
GEMINI_MODEL_ENV = "CANE_GEMINI_MODEL"
#: Latency knobs. The wearer is standing still waiting, so the defaults
#: favour speed: low thinking, medium image detail (enough for signs).
GEMINI_THINKING_ENV = "CANE_GEMINI_THINKING"
GEMINI_MEDIA_RES_ENV = "CANE_GEMINI_MEDIA_RES"
#: "auto": the model's own default for flash-lite (already fast), LOW for
#: anything else -- gemini-3.8-flash rejects MINIMAL ("not supported for this
#: model"), so LOW is its fastest accepted level.
DEFAULT_THINKING = "auto"
DEFAULT_MEDIA_RES = "medium"
#: Tried when the primary errors or overruns its budget. "none" disables.
GEMINI_FALLBACK_ENV = "CANE_GEMINI_FALLBACK_MODEL"
DEFAULT_FALLBACK_MODEL = "gemini-3.8-flash"
#: Hedging: the same request goes out again if nothing has come back after
#: this long. Live 2026-09-26: flash-lite answered in 1.1 s on one run and
#: sat for 9 s on the next -- a tail, not a trend -- so a second copy is
#: the cheap fix. Attempts start at 0, 1x and 2x this; first reply wins.
GEMINI_HEDGE_ENV = "CANE_GEMINI_HEDGE_S"
DEFAULT_HEDGE_S = 3.0
#: Copies of the primary sent at t=0. Live 2026-09-26, three runs in a row:
#: the first image request after start-up never answered while an identical
#: second copy answered in 1.4-2.6 s -- so two go out at once and the first
#: reply wins. flash-lite is cheap; halving the tail is worth the tokens.
GEMINI_PARALLEL_ENV = "CANE_GEMINI_PARALLEL"
DEFAULT_PARALLEL = 2
#: Google Search grounding for ``ask`` questions (weather, opening hours).
#: "off" disables. Live 2026-09-26 the key answered plain calls but got 429
#: for search, so a refused search is retried at once without it, and
#: search then rests for SEARCH_COOLDOWN_S instead of taxing every question.
GEMINI_SEARCH_ENV = "CANE_GEMINI_SEARCH"
SEARCH_COOLDOWN_S = 600.0
GEMINI_API_KEY_ENV = "GEMINI_API_KEY"
JPEG_MIME = "image/jpeg"
#: Low temperature: this is a lookup, not creative writing.
TEMPERATURE = 0.2

#: Which reply shape a call asks the model for: a wearer's answer
#: (:class:`VlmReply`) or a signage sweep (:class:`ScanReply`).
_ReplyT = TypeVar("_ReplyT", bound=BaseModel)


@dataclass(frozen=True)
class LabeledImage:
    """One JPEG plus the sentence that tells the model where it points."""

    label: str
    jpeg: bytes


class VlmReply(BaseModel):
    """What the model is asked to return (``response_schema``)."""

    model_config = ConfigDict(extra="ignore")

    status: str | None = None
    image_index: int | None = None
    x_center: float | None = None
    #: [ymin, xmin, ymax, xmax] scaled 0-1000, Gemini's native box format.
    box_2d: list[float] | None = None
    subject: str | None = None
    detail: str | None = None
    confidence: str | None = None


class SignRead(BaseModel):
    """One sign the model says it can read, in one image.

    Deliberately the same (``image_index``, ``x_center``) shape as
    :class:`VlmReply`: direction never crosses this boundary as words, so the
    scanner turns the pair into a bearing with ``View.bearing_at`` exactly as
    the query path does.
    """

    model_config = ConfigDict(extra="ignore")

    image_index: int | None = None
    x_center: float | None = None
    #: The sign's words, as printed. Not interpreted here.
    text: str | None = None
    confidence: str | None = None


class ScanReply(BaseModel):
    """What one sweep of the ring for signage returns.

    ``setting`` is carried on the same reply because the images needed to read
    signs are the images that say whether this is a corridor or a street --
    one call, two answers, and the scanner stops spending calls outdoors.
    """

    model_config = ConfigDict(extra="ignore")

    signs: list[SignRead] | None = None
    setting: str | None = None


class VisionLanguageModel(Protocol):
    @property
    def name(self) -> str: ...

    async def ask(
        self, prompt: str, images: Sequence[LabeledImage], *, search: bool = False
    ) -> VlmReply: ...


class SignScanningModel(Protocol):
    """What the scanner asks of a model: one unhedged sweep, never a question.

    Separate from :class:`VisionLanguageModel` so the scanner can be given a
    cheaper, single-call instance than the one answering the wearer.
    """

    @property
    def name(self) -> str: ...

    async def scan(self, prompt: str, images: Sequence[LabeledImage]) -> ScanReply: ...


class VlmUnavailableError(RuntimeError):
    """No key, no network, a refusal, or JSON that is not JSON."""


class GeminiVLM:
    """``google-genai`` async client. Imported lazily: the SDK is the
    optional ``scene`` extra and the test suite never needs it.

    Hedged: ``parallel`` copies of the primary at 0 s, then the fallback
    model after ``hedge_s`` with no reply; an attempt that fails starts the
    next one at once. The first good reply wins and the rest are cancelled;
    the caller's overall timeout bounds the lot. Each call is logged.
    """

    def __init__(
        self,
        api_key: str,
        model: str = DEFAULT_GEMINI_MODEL,
        thinking: str = DEFAULT_THINKING,
        media_resolution: str = DEFAULT_MEDIA_RES,
        fallback_model: str | None = DEFAULT_FALLBACK_MODEL,
        hedge_s: float = DEFAULT_HEDGE_S,
        parallel: int = DEFAULT_PARALLEL,
        search: bool = True,
    ) -> None:
        from google import genai
        from google.genai import types

        self._types = types
        self._client = genai.Client(api_key=api_key)
        self._model = model
        self._fallback = fallback_model if fallback_model and fallback_model != model else None
        self._hedge_s = hedge_s
        self._parallel = max(1, parallel)
        self._thinking_setting = thinking.strip().lower()
        self._thinking = (
            None if self._thinking_setting == "auto" else _enum_member(types.ThinkingLevel, thinking)
        )
        self._media_resolution = _enum_member(types.MediaResolution, f"MEDIA_RESOLUTION_{media_resolution}")
        self.search_enabled = search
        self._search_off_until = 0.0

    @classmethod
    def from_env(cls) -> GeminiVLM:
        api_key = os.environ.get(GEMINI_API_KEY_ENV)
        if not api_key:
            raise VlmUnavailableError(f"{GEMINI_API_KEY_ENV} is not set")
        fallback = os.environ.get(GEMINI_FALLBACK_ENV, DEFAULT_FALLBACK_MODEL).strip()
        return cls(
            api_key,
            os.environ.get(GEMINI_MODEL_ENV, DEFAULT_GEMINI_MODEL),
            thinking=os.environ.get(GEMINI_THINKING_ENV, DEFAULT_THINKING),
            media_resolution=os.environ.get(GEMINI_MEDIA_RES_ENV, DEFAULT_MEDIA_RES),
            fallback_model=None if fallback.lower() in {"", "none", "off"} else fallback,
            hedge_s=float(os.environ.get(GEMINI_HEDGE_ENV, DEFAULT_HEDGE_S)),
            parallel=int(os.environ.get(GEMINI_PARALLEL_ENV, DEFAULT_PARALLEL)),
            search=os.environ.get(GEMINI_SEARCH_ENV, "on").strip().lower() not in {"off", "0", "false", "no"},
        )

    @property
    def name(self) -> str:
        return self._model if self._fallback is None else f"{self._model} (fallback {self._fallback})"

    async def warm_up(self) -> None:
        """One tiny text call, so the first real question does not also pay
        for DNS, TLS and the connection pool. Failures are only logged.
        """
        started = time.monotonic()
        try:
            await self._client.aio.models.generate_content(
                model=self._model, contents="Reply with the word ok.",
                config=self._types.GenerateContentConfig(
                    automatic_function_calling=self._types.AutomaticFunctionCallingConfig(disable=True),
                ),
            )
            logger.info("%s warm in %.1fs", self._model, time.monotonic() - started)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - warm-up is best effort
            logger.warning("%s warm-up failed: %s", self._model, exc)

    async def ask(
        self, prompt: str, images: Sequence[LabeledImage], *, search: bool = False
    ) -> VlmReply:
        """``search``: ground on Google Search while it is available."""
        contents = self._contents(prompt, images)
        # (model, start offset): the parallel primaries at 0, then one more
        # attempt -- the fallback model when there is one -- at hedge_s.
        plan = [(self._model, 0.0)] * self._parallel + [(self._fallback or self._model, self._hedge_s)]
        loop = asyncio.get_running_loop()
        started = loop.time()
        pending: set[asyncio.Task[VlmReply]] = set()
        errors: list[str] = []
        searching: dict[asyncio.Task[VlmReply], bool] = {}
        launched = 0
        try:
            while True:
                due = started + plan[launched][1] if launched < len(plan) else 0.0
                if launched < len(plan) and (not pending or loop.time() >= due):
                    model = plan[launched][0]
                    if plan[launched][1] > 0:
                        logger.warning(
                            "no reply after %.1fs; also asking %s", loop.time() - started, model
                        )
                    launched += 1
                    use_search = search and self._search_available()
                    task = asyncio.create_task(
                        self._call(model, contents, thinking=self._thinking_for(model), search=use_search)
                    )
                    searching[task] = use_search
                    pending.add(task)
                    continue
                if not pending:
                    raise VlmUnavailableError("; ".join(errors) or "no attempt was made")
                wait_s = max(0.0, due - loop.time()) if launched < len(plan) else None
                done, pending = await asyncio.wait(
                    pending, timeout=wait_s, return_when=asyncio.FIRST_COMPLETED
                )
                for task in done:
                    error = task.exception()
                    if error is None:
                        return task.result()
                    errors.append(str(error))
                    logger.warning("%s", error)
                    if searching.get(task) and self._search_off_until <= loop.time():
                        # Quota, or a model that will not mix search with
                        # JSON: ask again plainly, now, and rest search.
                        self._search_off_until = loop.time() + SEARCH_COOLDOWN_S
                        logger.warning("search unavailable; answering without it for %.0f min",
                                       SEARCH_COOLDOWN_S / 60)
                        plan.insert(launched, (self._model, 0.0))
        finally:
            for task in pending:
                task.cancel()

    def _search_available(self) -> bool:
        return self.search_enabled and asyncio.get_running_loop().time() >= self._search_off_until

    def _thinking_for(self, model: str) -> object | None:
        if self._thinking_setting != "auto":
            return self._thinking
        return None if "lite" in model else self._types.ThinkingLevel.LOW

    def _contents(self, prompt: str, images: Sequence[LabeledImage]) -> list[object]:
        contents: list[object] = []
        for image in images:
            contents.append(image.label)
            contents.append(self._types.Part.from_bytes(data=image.jpeg, mime_type=JPEG_MIME))
        contents.append(prompt)
        return contents

    async def scan(self, prompt: str, images: Sequence[LabeledImage]) -> ScanReply:
        """One sweep of the ring for signage: a single call, deliberately.

        The hedging in :meth:`ask` exists because a wearer is standing still
        waiting for an answer. Nobody is waiting for a scan -- it runs in the
        background for the life of the walk -- so paying two or three times
        for every sweep would be spending the wearer's quota on impatience
        nobody feels. A failed sweep is simply the next sweep's problem.
        """
        return await self._generate(
            self._model,
            self._contents(prompt, images),
            thinking=self._thinking,
            search=False,
            schema=ScanReply,
        )

    async def _call(
        self, model: str, contents: list[object], *, thinking: object | None, search: bool = False
    ) -> VlmReply:
        return await self._generate(
            model, contents, thinking=thinking, search=search, schema=VlmReply
        )

    async def _generate(
        self,
        model: str,
        contents: list[object],
        *,
        thinking: object | None,
        search: bool = False,
        schema: type[_ReplyT],
    ) -> _ReplyT:
        types = self._types
        config = types.GenerateContentConfig(
            response_mime_type="application/json",
            response_schema=schema,
            temperature=TEMPERATURE,
            # Latency over depth: the wearer is standing still waiting.
            thinking_config=types.ThinkingConfig(thinking_level=thinking) if thinking else None,
            media_resolution=self._media_resolution,
            tools=[types.Tool(google_search=types.GoogleSearch())] if search else None,
            # Only built-in tools; keep the SDK out of its function-calling loop.
            automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),
        )
        started = time.monotonic()
        try:
            response = await self._client.aio.models.generate_content(
                model=model, contents=contents, config=config
            )
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - every SDK failure means "unavailable"
            # The SDK's message carries the whole JSON error body; the first
            # line ("429 RESOURCE_EXHAUSTED. ...") is the useful part.
            reason = str(exc).split(". {", 1)[0][:200]
            raise VlmUnavailableError(f"{model}{' with search' if search else ''} call failed: {reason}") from exc
        logger.info("%s%s answered in %.1fs", model, " with search" if search else "", time.monotonic() - started)
        parsed = response.parsed
        if isinstance(parsed, schema):
            return parsed
        text = response.text or ""
        try:
            return schema.model_validate_json(text)
        except ValueError as exc:
            raise VlmUnavailableError(f"{model} returned non-JSON: {text[:200]!r}") from exc


def _enum_member(enum: type, name: str) -> object | None:
    """``"minimal"`` -> ``ThinkingLevel.MINIMAL``; unknown or "none" -> ``None``
    (the model's default), logged rather than fatal.
    """
    key = name.strip().upper()
    if key.endswith("NONE") or key.endswith("DEFAULT"):
        return None
    member = getattr(enum, key, None)
    if member is None:
        logger.warning("unknown %s %r; using the model default", enum.__name__, name)
    return member


class FakeVLM:
    """Scripted: returns ``reply`` (or raises ``error``) after ``delay_s``,
    and records every call so tests can assert what was sent.
    """

    def __init__(
        self,
        reply: VlmReply | None = None,
        *,
        error: Exception | None = None,
        delay_s: float = 0.0,
        scan_reply: ScanReply | None = None,
        scan_replies: Sequence[ScanReply] | None = None,
        scan_error: Exception | None = None,
    ) -> None:
        self._reply = reply or VlmReply(status="not_found")
        self._error = error
        self._delay_s = delay_s
        #: A sweep at a time, so a test can walk a sign across the ring; the
        #: last one repeats once the script runs out.
        self._scan_replies = tuple(scan_replies) if scan_replies else ()
        self._scan_reply = scan_reply or ScanReply(signs=[], setting="indoor")
        self._scan_error = scan_error
        self.calls: list[tuple[str, tuple[LabeledImage, ...]]] = []
        self.searches: list[bool] = []
        self.scans: list[tuple[str, tuple[LabeledImage, ...]]] = []

    @property
    def name(self) -> str:
        return "fake"

    async def ask(
        self, prompt: str, images: Sequence[LabeledImage], *, search: bool = False
    ) -> VlmReply:
        self.calls.append((prompt, tuple(images)))
        self.searches.append(search)
        if self._delay_s:
            await asyncio.sleep(self._delay_s)
        if self._error is not None:
            raise self._error
        return self._reply

    async def scan(self, prompt: str, images: Sequence[LabeledImage]) -> ScanReply:
        index = len(self.scans)
        self.scans.append((prompt, tuple(images)))
        if self._delay_s:
            await asyncio.sleep(self._delay_s)
        if self._scan_error is not None:
            raise self._scan_error
        if not self._scan_replies:
            return self._scan_reply
        return self._scan_replies[min(index, len(self._scan_replies) - 1)]
