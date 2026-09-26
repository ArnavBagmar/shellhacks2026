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
from typing import Protocol, Sequence

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
GEMINI_API_KEY_ENV = "GEMINI_API_KEY"
JPEG_MIME = "image/jpeg"
#: Low temperature: this is a lookup, not creative writing.
TEMPERATURE = 0.2


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
    subject: str | None = None
    detail: str | None = None
    confidence: str | None = None


class VisionLanguageModel(Protocol):
    @property
    def name(self) -> str: ...

    async def ask(self, prompt: str, images: Sequence[LabeledImage]) -> VlmReply: ...


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

    async def ask(self, prompt: str, images: Sequence[LabeledImage]) -> VlmReply:
        contents = self._contents(prompt, images)
        # (model, start offset): the parallel primaries at 0, then one more
        # attempt -- the fallback model when there is one -- at hedge_s.
        plan = [(self._model, 0.0)] * self._parallel + [(self._fallback or self._model, self._hedge_s)]
        loop = asyncio.get_running_loop()
        started = loop.time()
        pending: set[asyncio.Task[VlmReply]] = set()
        errors: list[str] = []
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
                    pending.add(asyncio.create_task(
                        self._call(model, contents, thinking=self._thinking_for(model))
                    ))
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
        finally:
            for task in pending:
                task.cancel()

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

    async def _call(self, model: str, contents: list[object], *, thinking: object | None) -> VlmReply:
        types = self._types
        config = types.GenerateContentConfig(
            response_mime_type="application/json",
            response_schema=VlmReply,
            temperature=TEMPERATURE,
            # Latency over depth: the wearer is standing still waiting.
            thinking_config=types.ThinkingConfig(thinking_level=thinking) if thinking else None,
            media_resolution=self._media_resolution,
            # No tools are declared; keep the SDK out of its function-calling loop.
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
            raise VlmUnavailableError(f"{model} call failed: {exc}") from exc
        logger.info("%s answered in %.1fs", model, time.monotonic() - started)
        parsed = response.parsed
        if isinstance(parsed, VlmReply):
            return parsed
        text = response.text or ""
        try:
            return VlmReply.model_validate_json(text)
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
    ) -> None:
        self._reply = reply or VlmReply(status="not_found")
        self._error = error
        self._delay_s = delay_s
        self.calls: list[tuple[str, tuple[LabeledImage, ...]]] = []

    @property
    def name(self) -> str:
        return "fake"

    async def ask(self, prompt: str, images: Sequence[LabeledImage]) -> VlmReply:
        self.calls.append((prompt, tuple(images)))
        if self._delay_s:
            await asyncio.sleep(self._delay_s)
        if self._error is not None:
            raise self._error
        return self._reply
