"""The additive ``query`` (phone -> laptop) and ``answer`` (laptop -> phone)
frames of step 20: the golden fixture both languages run, and the server's
``on_query`` hook and ``send_answer``.
"""

from __future__ import annotations

import asyncio
import base64
import json
from pathlib import Path
from typing import Any

import pytest
import websockets
from jsonschema import Draft202012Validator

from cane.transport.messages import (
    MalformedMessageError,
    Ping,
    SceneAnswer,
    SceneQuery,
    SpeakRequest,
    VoiceClip,
    decode,
    encode,
)
from cane.transport.server import EventLinkServer

RECV_TIMEOUT_S = 2.0
FIXTURE_PATH = (
    Path(__file__).resolve().parents[3]
    / "contracts"
    / "fixtures"
    / "transport_query_answer.golden.json"
)


def _cases() -> list[dict[str, Any]]:
    with FIXTURE_PATH.open("r", encoding="utf-8") as f:
        return json.load(f)["cases"]


def _frame(name: str) -> dict[str, Any]:
    return next(case["frame"] for case in _cases() if case["name"] == name)


async def _recv_decoded(connection: Any) -> object:
    raw = await asyncio.wait_for(connection.recv(), timeout=RECV_TIMEOUT_S)
    return decode(raw)


async def _wait_for(predicate: Any, timeout_s: float = RECV_TIMEOUT_S) -> None:
    deadline = asyncio.get_running_loop().time() + timeout_s
    while asyncio.get_running_loop().time() < deadline:
        if predicate():
            return
        await asyncio.sleep(0.01)


# --------------------------------------------------------------------------
# golden fixture: the decoder and the schema agree on every case
# --------------------------------------------------------------------------


@pytest.mark.parametrize("case", _cases(), ids=lambda c: c["name"])
def test_the_decoder_matches_the_fixture(case: dict[str, Any]) -> None:
    text = json.dumps(case["frame"])
    if case["expect"] == "valid":
        assert isinstance(decode(text), (SceneQuery, SceneAnswer, SpeakRequest, VoiceClip))
    else:
        with pytest.raises(MalformedMessageError):
            decode(text)


@pytest.mark.parametrize("case", _cases(), ids=lambda c: c["name"])
def test_the_schema_matches_the_fixture(
    case: dict[str, Any], envelope_validator: Draft202012Validator
) -> None:
    assert envelope_validator.is_valid(case["frame"]) is (case["expect"] == "valid")


def test_an_answer_encodes_to_exactly_the_wire_shape() -> None:
    frame = _frame("answer_found")
    parsed = decode(json.dumps(frame))
    assert isinstance(parsed, SceneAnswer)

    assert json.loads(encode(parsed)) == frame


def test_a_bool_is_not_an_integer_timestamp() -> None:
    frame = {**_frame("query_find_no_frame"), "t_phone_ms": True}
    with pytest.raises(MalformedMessageError):
        decode(json.dumps(frame))


# --------------------------------------------------------------------------
# the server
# --------------------------------------------------------------------------


async def test_a_query_reaches_on_query_and_its_answer_reaches_the_phone() -> None:
    seen: list[SceneQuery] = []
    server_box: list[EventLinkServer] = []

    async def answer_it(query: SceneQuery) -> None:
        seen.append(query)
        await server_box[0].send_answer(
            SceneAnswer(
                query_id=query.query_id, status="not_found", subject=None,
                bearing_deg=None, detail=None, confidence=None, latency_ms=5,
            )
        )

    async with EventLinkServer(
        "127.0.0.1", 0, ping_interval_s=0.05, on_query=answer_it
    ) as srv:
        server_box.append(srv)
        async with websockets.connect(f"ws://127.0.0.1:{srv.port}") as phone:
            await _recv_decoded(phone)
            await phone.send(json.dumps(_frame("query_find_no_frame")))

            answer = None
            for _ in range(10):
                msg = await _recv_decoded(phone)
                if isinstance(msg, SceneAnswer):
                    answer = msg
                    break

    assert [q.target for q in seen] == ["bathroom"]
    assert answer is not None
    assert answer.query_id == "q-0001"
    assert answer.status == "not_found"


async def test_a_query_frame_carrying_a_large_photo_is_accepted() -> None:
    seen: list[SceneQuery] = []

    async def record(query: SceneQuery) -> None:
        seen.append(query)

    photo_b64 = base64.b64encode(b"\xff" * 1_900_000).decode("ascii")
    frame = _frame("query_find_with_phone_frame")
    frame = {**frame, "phone_frame": {**frame["phone_frame"], "jpeg_b64": photo_b64}}

    async with EventLinkServer("127.0.0.1", 0, ping_interval_s=0.05, on_query=record) as srv:
        async with websockets.connect(
            f"ws://127.0.0.1:{srv.port}", max_size=None
        ) as phone:
            await _recv_decoded(phone)
            await phone.send(json.dumps(frame))
            await _wait_for(lambda: bool(seen))

    assert len(seen) == 1
    assert seen[0].phone_frame is not None
    assert len(seen[0].phone_frame.jpeg_b64) == len(photo_b64)


async def test_a_raising_on_query_does_not_drop_the_phone() -> None:
    async def explode(query: SceneQuery) -> None:
        raise RuntimeError("the scene service blew up")

    async with EventLinkServer("127.0.0.1", 0, ping_interval_s=0.05, on_query=explode) as srv:
        async with websockets.connect(f"ws://127.0.0.1:{srv.port}") as phone:
            await _recv_decoded(phone)
            await phone.send(json.dumps(_frame("query_find_no_frame")))
            # The connection must stay up: pings keep arriving.
            assert isinstance(await _recv_decoded(phone), Ping)
            assert isinstance(await _recv_decoded(phone), Ping)


async def test_a_query_is_ignored_when_no_service_is_attached() -> None:
    async with EventLinkServer("127.0.0.1", 0, ping_interval_s=0.05) as srv:
        async with websockets.connect(f"ws://127.0.0.1:{srv.port}") as phone:
            await _recv_decoded(phone)
            await phone.send(json.dumps(_frame("query_find_no_frame")))
            await _wait_for(lambda: srv.frames_ignored == 1)

    assert srv.frames_ignored == 1


async def test_a_slow_query_does_not_stall_the_pong_stream() -> None:
    release = asyncio.Event()

    async def slow(query: SceneQuery) -> None:
        await release.wait()

    async with EventLinkServer("127.0.0.1", 0, ping_interval_s=0.05, on_query=slow) as srv:
        async with websockets.connect(f"ws://127.0.0.1:{srv.port}") as phone:
            await _recv_decoded(phone)
            await phone.send(json.dumps(_frame("query_find_no_frame")))
            assert isinstance(await _recv_decoded(phone), Ping)
            assert isinstance(await _recv_decoded(phone), Ping)
        release.set()


async def test_send_answer_with_no_phone_returns_false() -> None:
    async with EventLinkServer("127.0.0.1", 0, ping_interval_s=0.05) as srv:
        answer = decode(json.dumps(_frame("answer_unavailable")))
        assert isinstance(answer, SceneAnswer)
        assert await srv.send_answer(answer) is False


# --------------------------------------------------------------------------
# speak / voice: the natural voice for answers
# --------------------------------------------------------------------------


def test_a_voice_clip_encodes_to_exactly_the_wire_shape() -> None:
    frame = _frame("voice_ok")
    clip = decode(json.dumps(frame))
    assert isinstance(clip, VoiceClip)
    assert json.loads(encode(clip)) == frame


async def test_speak_with_no_voice_attached_is_answered_unavailable_at_once() -> None:
    # The phone must never wait out a timeout for a voice the laptop lacks.
    async with EventLinkServer("127.0.0.1", 0, ping_interval_s=10) as srv:
        async with websockets.connect(f"ws://127.0.0.1:{srv.port}") as phone:
            await phone.send(json.dumps(_frame("speak_valid")))
            clip = await _recv_decoded(phone)

    assert isinstance(clip, VoiceClip)
    assert clip.speak_id == "s-0001"
    assert clip.status == "unavailable"
    assert clip.audio_b64 is None


async def test_speak_reaches_on_speak_and_its_clip_reaches_the_phone() -> None:
    seen: list[SpeakRequest] = []

    async def voice_it(request: SpeakRequest) -> VoiceClip:
        seen.append(request)
        return VoiceClip(speak_id=request.speak_id, status="ok", format="mp3_44100_64",
                         audio_b64="SUQzBA==", latency_ms=300)

    async with EventLinkServer("127.0.0.1", 0, ping_interval_s=10, on_speak=voice_it) as srv:
        async with websockets.connect(f"ws://127.0.0.1:{srv.port}") as phone:
            await phone.send(json.dumps(_frame("speak_valid")))
            clip = await _recv_decoded(phone)

    assert [r.text for r in seen] == ["To your right, restroom sign."]
    assert isinstance(clip, VoiceClip)
    assert clip.status == "ok"
    assert clip.audio_b64 == "SUQzBA=="


async def test_a_raising_on_speak_still_answers_unavailable() -> None:
    async def explode(request: SpeakRequest) -> VoiceClip:
        raise RuntimeError("elevenlabs blew up")

    async with EventLinkServer("127.0.0.1", 0, ping_interval_s=10, on_speak=explode) as srv:
        async with websockets.connect(f"ws://127.0.0.1:{srv.port}") as phone:
            await phone.send(json.dumps(_frame("speak_valid")))
            clip = await _recv_decoded(phone)

    assert isinstance(clip, VoiceClip)
    assert clip.status == "unavailable"
