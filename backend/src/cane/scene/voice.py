"""The natural voice: ElevenLabs text-to-speech, on the laptop so the key
never reaches the phone.

The phone sends ``speak`` for answers and prompts; this turns the words into
an MP3 and the server sends it back as one ``voice`` frame. Hazards never
come here -- they are spoken on the phone, instantly and offline.

Measured live 2026-09-26 (one short answer, warm connection): Turbo v2.5
0.26 s, Flash v2.5 0.3-0.55 s, v3 Conversational 1.0 s. The wearer picked
Sarah on Turbo by ear. Every failure is ``None`` / ``unavailable``: the
phone's own voice is always the fallback.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import logging
import os
import time
from collections import OrderedDict
from pathlib import Path

import httpx

from cane.transport.messages import SPEAK_TEXT_MAX_CHARS, SpeakRequest, VoiceClip

logger = logging.getLogger(__name__)

API_KEY_ENV = "ELEVENLABS_API_KEY"
VOICE_ENV = "CANE_ELEVENLABS_VOICE"
MODEL_ENV = "CANE_ELEVENLABS_MODEL"
SWITCH_ENV = "CANE_VOICE"
#: Sarah -- "Mature, Reassuring, Confident".
DEFAULT_VOICE_ID = "EXAVITQu4vr4xnSDxMaL"
DEFAULT_MODEL = "eleven_turbo_v2_5"
#: MP3 plays everywhere (AVAudioPlayer); 64 kbps is ~8 KB per second of speech.
OUTPUT_FORMAT = "mp3_44100_64"
TTS_URL = "https://api.elevenlabs.io/v1/text-to-speech/{voice_id}"
#: Past this the phone has already given up (its budget is 2.5 s).
TIMEOUT_S = 4.0
CACHE_ENTRIES = 64
#: Clips already paid for, kept across runs (gitignored): backend/.cache/voice.
DISK_CACHE_DIR = Path(__file__).resolve().parents[3] / ".cache" / "voice"
#: ElevenLabs caps concurrent requests per plan: 5 at once at start-up got
#: one `429 concurrent_limit_exceeded` (2026-09-26). Two in flight is
#: plenty for one wearer; a refusal of that kind is retried once.
MAX_CONCURRENT = 2
RETRY_AFTER_S = 0.3
#: Said on every walk: synthesized at start-up so the first one is instant
#: (and the TLS connection is warm). ~600 characters of credit the first
#: time; later starts read them from the disk cache for free.
COMMON_PHRASES = (
    # = VoicePrefetch.phrases in ios/CaneKit/Sources/CaneAssist/VoicePrefetch.swift
    # (a Swift test checks every line is here): the phone fetches them all on
    # connect, so each plays at once in this voice.
    "Hold still, looking around.", "Hmm, let me look around.", "One sec, looking around.",
    "Okay, let me take a look.", "Hmm, give me a second.", "Let me see what's around.",
    "Hold still, looking ahead.", "Hmm, let me look ahead.", "One sec, checking in front of you.",
    "Okay, let me see what's ahead.",
    "Hmm, let me measure that.", "One sec, measuring.", "Let me see how far.", "Okay, checking the distance.",
    "Let me check.", "Hmm, one sec.", "Okay, let me see.", "Hmm, let me think.", "Good question, one sec.",
    "Still looking.", "Hang on, almost there.", "Hmm, still checking.", "Just a moment more.",
    "Yes?", "Mm-hm?", "I'm listening.", "Go ahead.",
    # "Guide my hand".
    "Reach out slowly. The beeps and your watch speed up as you get closer.",
    "Reach out slowly. The beeps speed up as you get closer.",
    "Reach out in front of you.", "You've got it.", "Finding it again.",
    "Let's stop there. Ask me again when you're ready.",
    "Left.", "Right.", "Up.", "Down.", "Forward.", "Back toward you.",
    # Fixed replies.
    "I can't reach the laptop right now.", "Sorry, that took too long.", "Okay.",
    "Turn to face it, then ask me again to guide your hand.", "What should I guide your hand to?",
)


class ElevenLabsVoice:
    def __init__(
        self,
        api_key: str,
        *,
        voice_id: str = DEFAULT_VOICE_ID,
        model: str = DEFAULT_MODEL,
        client: httpx.AsyncClient | None = None,
        cache_dir: Path | None = None,
    ) -> None:
        self._cache_dir = cache_dir
        self._api_key = api_key
        self._voice_id = voice_id
        self._model = model
        self._client = client or httpx.AsyncClient(timeout=TIMEOUT_S)
        self._cache: OrderedDict[str, bytes] = OrderedDict()
        self._in_flight: dict[str, asyncio.Task[bytes | None]] = {}
        self._slots = asyncio.Semaphore(MAX_CONCURRENT)

    @classmethod
    def from_env(cls) -> ElevenLabsVoice | None:
        """``None`` without a key or with ``CANE_VOICE=off``."""
        if os.environ.get(SWITCH_ENV, "on").strip().lower() in {"off", "0", "false", "no"}:
            return None
        key = os.environ.get(API_KEY_ENV, "").strip().strip('"')
        if not key:
            return None
        return cls(
            key,
            voice_id=os.environ.get(VOICE_ENV, "").strip() or DEFAULT_VOICE_ID,
            model=os.environ.get(MODEL_ENV, "").strip() or DEFAULT_MODEL,
            cache_dir=DISK_CACHE_DIR,
        )

    @property
    def name(self) -> str:
        return f"ElevenLabs {self._model} voice {self._voice_id}"

    async def warm_up(self) -> None:
        await asyncio.gather(*(self.synthesize(text) for text in COMMON_PHRASES))
        logger.info("%s ready (%d common phrases cached)", self.name, len(self._cache))

    def prefetch(self, text: str) -> None:
        """Start synthesizing ``text`` now (e.g. an answer about to be sent),
        so the phone's ``speak`` for it joins the request already in flight.
        """
        asyncio.ensure_future(self.synthesize(text))

    async def clip(self, request: SpeakRequest) -> VoiceClip:
        started = time.monotonic()
        audio = await self.synthesize(request.text)
        latency_ms = round((time.monotonic() - started) * 1000)
        if audio is None:
            return VoiceClip.unavailable(request.speak_id, latency_ms)
        return VoiceClip(
            speak_id=request.speak_id, status="ok", format=OUTPUT_FORMAT,
            audio_b64=base64.b64encode(audio).decode("ascii"), latency_ms=latency_ms,
        )

    async def synthesize(self, text: str) -> bytes | None:
        """MP3 bytes for ``text``: cached, one request per distinct text at a
        time, ``None`` on any failure (never raises, never cached).
        """
        text = text.strip()[:SPEAK_TEXT_MAX_CHARS]
        if not text:
            return None
        if text in self._cache:
            self._cache.move_to_end(text)
            return self._cache[text]
        stored = self._read_disk(text)
        if stored is not None:
            self._remember(text, stored)
            return stored
        task = self._in_flight.get(text)
        if task is None:
            task = asyncio.ensure_future(self._fetch(text))
            self._in_flight[text] = task
            task.add_done_callback(lambda _: self._in_flight.pop(text, None))
        return await asyncio.shield(task)

    async def _fetch(self, text: str) -> bytes | None:
        started = time.monotonic()
        try:
            async with self._slots:
                response = await self._post(text)
                if response.status_code == 429 and "concurrent" in response.text:
                    await asyncio.sleep(RETRY_AFTER_S)
                    response = await self._post(text)
        except httpx.HTTPError as exc:
            logger.warning("voice: request failed (%s) for %r", exc, text[:60])
            return None
        if response.status_code != 200 or not response.content:
            logger.warning("voice: HTTP %d %s for %r", response.status_code, response.text[:120], text[:60])
            return None
        audio = response.content
        self._remember(text, audio)
        self._write_disk(text, audio)
        logger.info("voice: %d chars -> %d KB in %.2fs", len(text), len(audio) // 1024, time.monotonic() - started)
        return audio

    def _remember(self, text: str, audio: bytes) -> None:
        self._cache[text] = audio
        while len(self._cache) > CACHE_ENTRIES:
            self._cache.popitem(last=False)

    def _disk_path(self, text: str) -> Path | None:
        if self._cache_dir is None:
            return None
        key = hashlib.sha256(f"{self._voice_id}|{self._model}|{OUTPUT_FORMAT}|{text}".encode()).hexdigest()[:32]
        return self._cache_dir / f"{key}.mp3"

    def _read_disk(self, text: str) -> bytes | None:
        path = self._disk_path(text)
        try:
            return path.read_bytes() if path is not None and path.is_file() else None
        except OSError:
            return None

    def _write_disk(self, text: str, audio: bytes) -> None:
        path = self._disk_path(text)
        if path is None:
            return
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(audio)
        except OSError as exc:
            logger.warning("voice: could not cache to %s: %s", path, exc)

    async def _post(self, text: str) -> httpx.Response:
        return await self._client.post(
            TTS_URL.format(voice_id=self._voice_id),
            params={"output_format": OUTPUT_FORMAT},
            headers={"xi-api-key": self._api_key},
            json={"text": text, "model_id": self._model},
            timeout=TIMEOUT_S,
        )
