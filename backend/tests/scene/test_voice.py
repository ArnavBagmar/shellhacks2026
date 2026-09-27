"""ElevenLabs behind ``ElevenLabsVoice``, with the HTTP layer mocked: the
request it makes, the cache, one request for concurrent asks of the same
words, and that every failure is ``None`` (the phone then uses its own voice).
"""

from __future__ import annotations

import asyncio
import base64

import httpx
import pytest

from cane.scene.voice import DEFAULT_MODEL, DEFAULT_VOICE_ID, OUTPUT_FORMAT, ElevenLabsVoice
from cane.transport.messages import SpeakRequest

MP3 = b"ID3\x04fake mp3 bytes"


def _voice(requests: list[httpx.Request], *, status: int = 200, delay_s: float = 0.0,
           fail: bool = False) -> ElevenLabsVoice:
    async def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if delay_s:
            await asyncio.sleep(delay_s)
        if fail:
            raise httpx.ConnectError("no network")
        return httpx.Response(status, content=MP3 if status == 200 else b'{"detail":"quota"}')

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return ElevenLabsVoice("test-key", client=client)


async def test_it_asks_for_the_chosen_voice_model_and_format() -> None:
    requests: list[httpx.Request] = []
    audio = await _voice(requests).synthesize("To your right, restroom sign.")

    assert audio == MP3
    (request,) = requests
    assert request.url.path == f"/v1/text-to-speech/{DEFAULT_VOICE_ID}"
    assert request.url.params["output_format"] == OUTPUT_FORMAT
    assert request.headers["xi-api-key"] == "test-key"
    body = request.read().decode()
    assert '"model_id":"' + DEFAULT_MODEL + '"' in body.replace(" ", "")
    assert "restroom sign" in body


def test_the_default_is_sarah_on_turbo() -> None:
    # Picked by ear from the samples, 2026-09-26: Sarah, eleven_turbo_v2_5 (0.26 s).
    assert DEFAULT_VOICE_ID == "EXAVITQu4vr4xnSDxMaL"
    assert DEFAULT_MODEL == "eleven_turbo_v2_5"


async def test_the_same_words_are_synthesized_once() -> None:
    requests: list[httpx.Request] = []
    voice = _voice(requests)
    await voice.synthesize("Let me check.")
    await voice.synthesize("Let me check.")
    assert len(requests) == 1


async def test_concurrent_asks_for_the_same_words_share_one_request() -> None:
    requests: list[httpx.Request] = []
    voice = _voice(requests, delay_s=0.05)
    first, second = await asyncio.gather(voice.synthesize("Paris."), voice.synthesize("Paris."))
    assert first == second == MP3
    assert len(requests) == 1


@pytest.mark.parametrize("kwargs", [{"status": 401}, {"status": 429}, {"fail": True}])
async def test_every_failure_is_none(kwargs: dict[str, object]) -> None:
    assert await _voice([], **kwargs).synthesize("Paris.") is None  # type: ignore[arg-type]


async def test_a_failure_is_not_cached() -> None:
    requests: list[httpx.Request] = []
    voice = _voice(requests, status=500)
    await voice.synthesize("Paris.")
    await voice.synthesize("Paris.")
    assert len(requests) == 2


async def test_a_clip_frame_for_a_speak_request() -> None:
    clip = await _voice([]).clip(SpeakRequest(speak_id="s-1", text="Paris."))
    assert clip.status == "ok"
    assert clip.format == OUTPUT_FORMAT
    assert base64.b64decode(clip.audio_b64 or "") == MP3

    broken = await _voice([], status=500).clip(SpeakRequest(speak_id="s-2", text="Paris."))
    assert broken.status == "unavailable"
    assert broken.audio_b64 is None and broken.format is None


def test_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ELEVENLABS_API_KEY", raising=False)
    assert ElevenLabsVoice.from_env() is None
    monkeypatch.setenv("ELEVENLABS_API_KEY", "k")
    assert ElevenLabsVoice.from_env() is not None
    monkeypatch.setenv("CANE_VOICE", "off")
    assert ElevenLabsVoice.from_env() is None


async def test_a_concurrency_refusal_is_retried_once() -> None:
    requests: list[httpx.Request] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if len(requests) == 1:
            return httpx.Response(429, json={"detail": {"code": "concurrent_limit_exceeded"}})
        return httpx.Response(200, content=MP3)

    voice = ElevenLabsVoice("k", client=httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    assert await voice.synthesize("Paris.") == MP3
    assert len(requests) == 2


async def test_never_more_than_two_requests_at_once() -> None:
    live = peak = 0

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal live, peak
        live += 1
        peak = max(peak, live)
        await asyncio.sleep(0.02)
        live -= 1
        return httpx.Response(200, content=MP3)

    voice = ElevenLabsVoice("k", client=httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    await asyncio.gather(*(voice.synthesize(f"phrase {i}") for i in range(6)))
    assert peak == 2


async def test_a_clip_is_kept_on_disk_across_runs(tmp_path) -> None:  # type: ignore[no-untyped-def]
    requests: list[httpx.Request] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, content=MP3)

    def fresh() -> ElevenLabsVoice:
        return ElevenLabsVoice("k", client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
                               cache_dir=tmp_path)

    assert await fresh().synthesize("Hmm, one sec.") == MP3
    assert await fresh().synthesize("Hmm, one sec.") == MP3  # a new run: read from disk
    assert len(requests) == 1
    assert len(list(tmp_path.glob("*.mp3"))) == 1
