"""The local debug page: what it records per question, what it takes from
the phone's diagnostics, and that it serves over HTTP.
"""

from __future__ import annotations

import base64
import json
import logging
import urllib.request

import cv2
import numpy as np

from cane.scene.debug_view import DebugHub, PhoneDiagHandler, serve_debug
from cane.scene.frames import StillProvider
from cane.scene.service import SceneQueryService
from cane.scene.vlm import FakeVLM, VlmReply
from cane.transport.messages import PhoneFrame, SceneQuery

EQUIRECT = np.full((180, 360, 3), 90, dtype=np.uint8)
PHOTO = cv2.imencode(".jpg", np.full((120, 160, 3), 200, dtype=np.uint8))[1].tobytes()


def _query(query_id: str = "q-dbg1") -> SceneQuery:
    return SceneQuery(
        query_id=query_id, kind="find", target="sprite can", utterance="how far is the sprite can",
        t_phone_ms=0,
        phone_frame=PhoneFrame(jpeg_b64=base64.b64encode(PHOTO).decode(), hfov_deg=60.0, width=160, height=120),
    )


def _found_in_photo() -> VlmReply:
    return VlmReply(status="found", image_index=6, x_center=0.3, box_2d=[400, 200, 700, 400],
                    subject="sprite can", confidence="high")


async def _asked(hub: DebugHub, reply: VlmReply) -> None:
    service = SceneQueryService(FakeVLM(reply), StillProvider(EQUIRECT), settle_s=0.0, crop_px=64, debug=hub)
    await service.answer(_query())


async def test_every_image_sent_is_kept_and_the_pick_is_marked() -> None:
    hub = DebugHub()
    await _asked(hub, _found_in_photo())

    (q,) = hub.state()["queries"]
    assert len(q["labels"]) == 7  # six 360 views + the phone photo
    assert q["phone_views"] == [6]
    assert q["chosen"] == 6
    assert q["answer"]["phone_box"] is not None
    assert hub.state()["gemini"]["picked_phone"] == 1


async def test_the_picked_image_has_the_box_drawn_on_it() -> None:
    hub = DebugHub()
    await _asked(hub, _found_in_photo())

    drawn = cv2.imdecode(np.frombuffer(hub.query_image("q-dbg1", 6) or b"", np.uint8), cv2.IMREAD_COLOR)
    plain = cv2.imdecode(np.frombuffer(PHOTO, np.uint8), cv2.IMREAD_COLOR)
    assert drawn.shape == plain.shape
    assert np.abs(drawn.astype(int) - plain.astype(int)).max() > 60  # a yellow box, not JPEG noise
    assert hub.query_image("q-dbg1", 0) is not None
    assert hub.query_image("q-nope", 0) is None


async def test_a_not_found_answer_marks_no_pick() -> None:
    hub = DebugHub()
    await _asked(hub, VlmReply(status="not_found", image_index=2))
    assert hub.state()["queries"][0]["chosen"] is None


async def test_the_phone_side_is_attached_to_the_latest_question() -> None:
    hub = DebugHub()
    await _asked(hub, _found_in_photo())
    hub.on_phone_diag({"kind": "say", "data": {"text": "Hold still, looking around."}})
    hub.on_phone_diag({"kind": "answer", "data": {"query_id": "q-dbg1", "status": "found"}})
    hub.on_phone_diag({"kind": "distance", "data": {"outcome": "measured", "metres": 1.13}})
    hub.on_phone_diag({"kind": "say", "data": {"text": "Ahead to the left, sprite can, about 4 feet away."}})

    phone = hub.state()["queries"][0]["phone"]
    assert phone["said"]["text"].startswith("Ahead to the left")
    assert phone["distance"]["metres"] == 1.13


def test_phone_diagnostics_feed_the_stats_and_the_feed() -> None:
    hub = DebugHub()
    logger = logging.getLogger("cane.phone.test")
    logger.setLevel(logging.INFO)
    logger.addHandler(PhoneDiagHandler(hub))
    for line in (
        {"kind": "mic", "data": {"peak": 0.7}},
        {"kind": "depth", "data": {"metres": 1.4, "coverage": 0.9, "accuracy": "absolute", "trustworthy": "yes"}},
        {"kind": "voice", "data": {"source": "elevenlabs", "waited_ms": 120}},
        {"kind": "transcript", "data": {"text": " Hey", "final": False}},
        {"kind": "transcript", "data": {"text": " Hey Kane, what time is it?", "final": True}},
        {"kind": "intent", "data": {"command": "what time is it", "intent": "time"}},
    ):
        logger.info(json.dumps(line))

    state = hub.state()
    assert state["phone"]["connected"]
    assert state["phone"]["mic_peak"] == 0.7
    assert state["phone"]["depth"]["metres"] == 1.4
    assert state["voice"]["by_source"] == {"elevenlabs": 1}
    assert [e["kind"] for e in state["feed"]] == ["transcript", "intent"]  # partials left out


def test_the_live_strip_is_six_views_wide() -> None:
    hub = DebugHub(live_frame=lambda: EQUIRECT, source_name="still")
    strip = cv2.imdecode(np.frombuffer(hub.live_jpeg() or b"", np.uint8), cv2.IMREAD_COLOR)
    # Six tall views (what Gemini is sent): wider than tall overall, each taller than wide.
    assert strip.shape[1] == 6 * 240
    assert strip.shape[0] > 240
    assert hub.state()["x4"]["resolution"] == "360x180"


def test_a_live_frame_that_is_not_equirect_is_reported_not_raised() -> None:
    hub = DebugHub(live_frame=lambda: np.zeros((100, 100, 3), np.uint8), source_name="x4")
    assert hub.live_jpeg() is None
    assert "2:1" in hub.state()["x4"]["problem"]


async def test_it_serves_the_page_the_state_and_the_images() -> None:
    hub = DebugHub(live_frame=lambda: EQUIRECT)
    await _asked(hub, _found_in_photo())
    server = serve_debug(hub, port=0)
    try:
        base = f"http://127.0.0.1:{server.server_address[1]}"
        page = urllib.request.urlopen(f"{base}/").read()
        assert b"Cane Live View" in page
        state = json.loads(urllib.request.urlopen(f"{base}/state.json").read())
        assert state["queries"][0]["query_id"] == "q-dbg1"
        assert urllib.request.urlopen(f"{base}/query/q-dbg1/6.jpg").read()[:2] == b"\xff\xd8"
        assert urllib.request.urlopen(f"{base}/live.jpg").read()[:2] == b"\xff\xd8"
    finally:
        server.shutdown()


def test_the_live_view_is_a_motion_jpeg_stream() -> None:
    frames = iter(np.full((180, 360, 3), v, dtype=np.uint8) for v in range(0, 250, 10))
    hub = DebugHub(live_frame=lambda: next(frames), source_name="x4")
    server = serve_debug(hub, port=0)
    try:
        stream = urllib.request.urlopen(f"http://127.0.0.1:{server.server_address[1]}/live.mjpg", timeout=5)
        assert stream.headers["Content-Type"].startswith("multipart/x-mixed-replace")
        parts = 0
        buffered = b""
        while parts < 3:  # three distinct frames arrive without asking again
            buffered += stream.read1(65536)
            parts = buffered.count(b"Content-Type: image/jpeg")
        stream.close()
    finally:
        server.shutdown()


def test_a_still_picture_keeps_streaming_so_the_browser_shows_it() -> None:
    hub = DebugHub(live_frame=lambda: EQUIRECT, source_name="still test photo")
    server = serve_debug(hub, port=0)
    try:
        stream = urllib.request.urlopen(f"http://127.0.0.1:{server.server_address[1]}/live.mjpg", timeout=5)
        buffered = b""
        while buffered.count(b"--canelive") < 2:  # the second part is what makes Chrome draw the first
            buffered += stream.read1(65536)
    finally:
        server.shutdown()
