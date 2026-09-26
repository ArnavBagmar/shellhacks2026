"""SceneQueryService with a scripted VLM and a still frame: which images are
sent, how the model's (image, x) becomes a bearing, and that every failure
is an ``unavailable`` answer rather than an exception or silence.
"""

from __future__ import annotations

import asyncio
import base64
import threading

import numpy as np
import pytest

from cane.bridge.capture.latest import LatestFrameStore
from cane.scene.frames import GrabProvider, StillProvider
from cane.scene.service import SceneQueryService
from cane.scene.vlm import FakeVLM, VlmReply, VlmUnavailableError
from cane.transport.messages import PhoneFrame, SceneQuery

EQUIRECT = np.zeros((180, 360, 3), dtype=np.uint8)
CROP_PX = 32
PHONE_HFOV = 70.0
TINY_JPEG_B64 = base64.b64encode(b"\xff\xd8\xff\xe0 not really a jpeg").decode("ascii")


def _query(
    kind: str = "find", *, phone: bool = False, query_id: str = "q-1",
    utterance: str = "is there a bathroom around",
) -> SceneQuery:
    return SceneQuery(
        query_id=query_id,
        kind=kind,  # type: ignore[arg-type]
        target="bathroom" if kind == "find" else None,
        utterance=utterance,
        t_phone_ms=1_790_000_000_000,
        phone_frame=PhoneFrame(jpeg_b64=TINY_JPEG_B64, hfov_deg=PHONE_HFOV, width=4, height=3)
        if phone
        else None,
    )


def _service(vlm: FakeVLM, *, frames: object = "still", **kwargs: object) -> SceneQueryService:
    provider = StillProvider(EQUIRECT) if frames == "still" else frames
    options: dict[str, object] = {"settle_s": 0.0, "crop_px": CROP_PX, **kwargs}
    return SceneQueryService(vlm, provider, **options)  # type: ignore[arg-type]


def _found(index: int, x: float, **extra: object) -> VlmReply:
    return VlmReply(status="found", image_index=index, x_center=x, subject="restroom sign",
                    detail="past the elevators", confidence="medium", **extra)


# ------------------------------------------------------------------ images


async def test_a_find_sends_all_six_crops_labelled_by_direction() -> None:
    vlm = FakeVLM(_found(0, 0.5))
    await _service(vlm).answer(_query())

    (prompt, images), = vlm.calls
    assert len(images) == 6
    assert images[0].label.startswith("Image 0") and "straight ahead" in images[0].label
    assert "60 degrees to the right" in images[1].label
    assert "directly behind" in images[3].label
    assert "60 degrees to the left" in images[5].label
    assert all(image.jpeg[:2] == b"\xff\xd8" for image in images)
    assert "bathroom" in prompt


async def test_the_phone_photo_is_sent_last_and_untouched() -> None:
    vlm = FakeVLM(_found(0, 0.5))
    await _service(vlm).answer(_query(phone=True))

    (_, images), = vlm.calls
    assert len(images) == 7
    assert images[6].label.startswith("Image 6: phone camera")
    assert images[6].jpeg == base64.b64decode(TINY_JPEG_B64)


async def test_describe_front_with_a_photo_sends_only_the_photo() -> None:
    vlm = FakeVLM(VlmReply(status="described", detail="a hallway"))
    await _service(vlm).answer(_query("describe_front", phone=True))

    (_, images), = vlm.calls
    assert [image.label.split(":")[0] for image in images] == ["Image 0"]
    assert "phone camera" in images[0].label


async def test_describe_front_without_a_photo_sends_the_three_forward_crops() -> None:
    vlm = FakeVLM(VlmReply(status="described", detail="a hallway"))
    await _service(vlm).answer(_query("describe_front"))

    (_, images), = vlm.calls
    assert len(images) == 3
    assert "60 degrees to the left" in images[0].label
    assert "straight ahead" in images[1].label
    assert "60 degrees to the right" in images[2].label


# ----------------------------------------------------------------- bearing


@pytest.mark.parametrize(
    ("index", "x", "bearing"),
    [
        (0, 0.5, 0.0),
        (1, 0.75, 75.0),     # right front-quarter, right of its centre
        (5, 0.0, -90.0),     # left front-quarter, its left edge
        (3, 1.0, -150.0),    # rear crop wraps the seam: 180 + 30 -> -150
        (3, 0.0, 150.0),
    ],
)
async def test_the_image_and_x_position_become_a_torso_bearing(
    index: int, x: float, bearing: float
) -> None:
    answer = await _service(FakeVLM(_found(index, x))).answer(_query())

    assert answer.status == "found"
    assert answer.bearing_deg == pytest.approx(bearing)


async def test_negative_is_the_wearers_left_on_the_phone_photo_too() -> None:
    answer = await _service(FakeVLM(_found(6, 0.0))).answer(_query(phone=True))

    assert answer.bearing_deg == pytest.approx(-PHONE_HFOV / 2)


async def test_the_mounting_calibration_shifts_x4_bearings() -> None:
    answer = await _service(FakeVLM(_found(1, 0.5)), delta_deg=-10.0).answer(_query())

    assert answer.bearing_deg == pytest.approx(50.0)


async def test_an_out_of_range_image_index_keeps_the_find_but_drops_the_bearing() -> None:
    answer = await _service(FakeVLM(_found(9, 0.5))).answer(_query())

    assert answer.status == "found"
    assert answer.bearing_deg is None


# ------------------------------------------------------------ reply mapping


async def test_a_found_reply_maps_every_field() -> None:
    answer = await _service(FakeVLM(_found(1, 0.5))).answer(_query())

    assert answer.query_id == "q-1"
    assert answer.subject == "restroom sign"
    assert answer.detail == "past the elevators"
    assert answer.confidence == "medium"
    assert answer.latency_ms >= 0


async def test_a_found_reply_without_a_subject_falls_back_to_the_target() -> None:
    reply = VlmReply(status="FOUND", image_index=0, x_center=0.5, subject="  ", confidence="very")
    answer = await _service(FakeVLM(reply)).answer(_query())

    assert answer.status == "found"
    assert answer.subject == "bathroom"
    assert answer.confidence is None


async def test_anything_but_found_is_not_found_with_no_position() -> None:
    reply = VlmReply(status="maybe", image_index=2, x_center=0.5, subject="door")
    answer = await _service(FakeVLM(reply)).answer(_query())

    assert answer.status == "not_found"
    assert (answer.subject, answer.bearing_deg, answer.detail) == (None, None, None)


async def test_a_description_is_whitespace_normalised_and_capped() -> None:
    reply = VlmReply(status="described", detail="  a   long\nhallway " + "x" * 400)
    answer = await _service(FakeVLM(reply)).answer(_query("describe_around"))

    assert answer.status == "described"
    assert answer.detail is not None
    assert answer.detail.startswith("a long hallway")
    assert len(answer.detail) == 240


async def test_an_empty_description_is_unavailable() -> None:
    answer = await _service(FakeVLM(VlmReply(status="described", detail="null"))).answer(
        _query("describe_around")
    )

    assert answer.status == "unavailable"


# ---------------------------------------------------------------- failures


async def test_a_vlm_error_is_unavailable() -> None:
    vlm = FakeVLM(error=VlmUnavailableError("no network"))
    answer = await _service(vlm).answer(_query())

    assert answer.status == "unavailable"


async def test_a_slow_vlm_is_cut_off_and_unavailable() -> None:
    vlm = FakeVLM(_found(0, 0.5), delay_s=1.0)
    answer = await _service(vlm, vlm_timeout_s=0.05).answer(_query())

    assert answer.status == "unavailable"


async def test_no_frame_and_no_photo_is_unavailable_without_calling_the_vlm() -> None:
    vlm = FakeVLM(_found(0, 0.5))
    answer = await _service(vlm, frames=None).answer(_query())

    assert answer.status == "unavailable"
    assert vlm.calls == []


async def test_the_phone_photo_alone_is_enough_when_the_x4_is_gone() -> None:
    vlm = FakeVLM(_found(0, 1.0))
    answer = await _service(vlm, frames=None).answer(_query(phone=True))

    (_, images), = vlm.calls
    assert len(images) == 1
    assert answer.bearing_deg == pytest.approx(PHONE_HFOV / 2)


async def test_a_photo_that_is_not_base64_is_ignored() -> None:
    query = _query(phone=True).model_copy(
        update={"phone_frame": PhoneFrame(jpeg_b64="!!!!", hfov_deg=70.0, width=1, height=1)}
    )
    vlm = FakeVLM(_found(0, 0.5))
    await _service(vlm).answer(query)

    (_, images), = vlm.calls
    assert len(images) == 6


async def test_a_frame_that_is_not_equirect_is_skipped_not_fatal() -> None:
    vlm = FakeVLM(_found(0, 0.5))
    answer = await _service(vlm, frames=StillProvider(np.zeros((10, 10, 3), np.uint8))).answer(
        _query(phone=True)
    )

    assert answer.status == "found"
    (_, images), = vlm.calls
    assert len(images) == 1


# ------------------------------------------------------------- supersede


async def test_a_newer_query_supersedes_the_one_in_flight() -> None:
    vlm = FakeVLM(_found(0, 0.5), delay_s=0.3)
    service = _service(vlm)

    first = asyncio.create_task(service.handle(_query(query_id="q-old")))
    await asyncio.sleep(0.05)
    second = asyncio.create_task(service.handle(_query(query_id="q-new")))

    assert await first is None
    newer = await second
    assert newer is not None and newer.query_id == "q-new"


# ------------------------------------------------------------ live frames


async def test_the_grab_provider_returns_a_frame_captured_after_the_call() -> None:
    store = LatestFrameStore()
    old = np.zeros((2, 4, 3), np.uint8)
    new = np.ones((2, 4, 3), np.uint8)
    store.push(old, t_arrival_laptop_ms=1)

    timer = threading.Timer(0.05, lambda: store.push(new, t_arrival_laptop_ms=2))
    timer.start()
    try:
        image = await GrabProvider(store).fresh_equirect()
    finally:
        timer.cancel()

    assert image is not None and image.max() == 1


async def test_the_grab_provider_falls_back_to_the_latest_frame() -> None:
    store = LatestFrameStore()
    store.push(np.full((2, 4, 3), 7, np.uint8), t_arrival_laptop_ms=1)

    image = await GrabProvider(store).fresh_equirect()

    assert image is not None and image.max() == 7


def test_bearings_come_out_rounded_to_a_tenth() -> None:
    from cane.scene.service import View
    from cane.scene.vlm import LabeledImage

    view = View(image=LabeledImage("x", b""), centre_deg=0.0, fov_deg=60.0)
    assert view.bearing_at(0.77) == 16.2
    wrap = View(image=LabeledImage("x", b""), centre_deg=180.0, fov_deg=60.0)
    assert wrap.bearing_at(0.77) == -163.8


async def test_save_views_writes_what_the_model_saw(tmp_path: object) -> None:
    from pathlib import Path

    import json

    folder = Path(str(tmp_path))
    service = SceneQueryService(
        FakeVLM(_found(1, 0.5)), StillProvider(EQUIRECT), settle_s=0.0, crop_px=CROP_PX, dump_dir=folder
    )
    await service.answer(_query())

    saved = folder / "q-1"
    assert sorted(p.name for p in saved.glob("image-*.jpg")) == [f"image-{i}.jpg" for i in range(6)]
    summary = json.loads((saved / "query.json").read_text())
    assert summary["answer"]["bearing_deg"] == 60.0
    assert "bathroom" in summary["prompt"]


# --------------------------------------------------------------------- ask


async def test_ask_sends_every_view_with_the_question_and_where_and_when() -> None:
    from datetime import datetime

    vlm = FakeVLM(VlmReply(status="answered", detail="Cloudy, and the ground looks wet."))
    service = _service(vlm, location="FIU, Miami", now=lambda: datetime(2026, 9, 26, 15, 7))
    await service.answer(_query("ask", phone=True, utterance="how's the weather"))

    (prompt, images), = vlm.calls
    assert len(images) == 7
    assert "how's the weather" in prompt
    assert "FIU, Miami" in prompt
    assert "Saturday 26 September 2026" in prompt and "3:07 PM" in prompt
    assert vlm.searches == [True]


async def test_only_ask_may_search() -> None:
    vlm = FakeVLM(_found(0, 0.5))
    await _service(vlm).answer(_query())
    assert vlm.searches == [False]


async def test_an_ask_answer_is_spoken_as_given() -> None:
    vlm = FakeVLM(VlmReply(status="answered", detail="Paris."))
    answer = await _service(vlm).answer(_query("ask", utterance="what is the capital of france"))
    assert answer.status == "described"
    assert answer.detail == "Paris."
    assert answer.bearing_deg is None


async def test_an_ask_about_one_visible_thing_gets_a_bearing() -> None:
    vlm = FakeVLM(_found(1, 0.5))
    answer = await _service(vlm).answer(_query("ask", utterance="do you see a bathroom nearby"))
    assert answer.status == "found"
    assert answer.subject == "restroom sign"
    assert answer.bearing_deg == 60.0


async def test_an_empty_ask_answer_is_unavailable() -> None:
    vlm = FakeVLM(VlmReply(status="answered"))
    answer = await _service(vlm).answer(_query("ask", utterance="hmm"))
    assert answer.status == "unavailable"


class _Weather:
    def __init__(self) -> None:
        self.calls = 0

    async def summary(self) -> str | None:
        self.calls += 1
        return "Live weather for Miami, Florida: 85°F and mainly clear."


async def test_a_weather_question_gets_live_weather_in_the_prompt() -> None:
    weather = _Weather()
    vlm = FakeVLM(VlmReply(status="answered", detail="85 and clear."))
    await _service(vlm, weather=weather).answer(_query("ask", utterance="how is the weather today"))
    (prompt, _), = vlm.calls
    assert "85°F and mainly clear" in prompt
    assert weather.calls == 1
    # The facts are already here; a web search would only add latency.
    assert vlm.searches == [False]


async def test_other_questions_do_not_fetch_weather() -> None:
    weather = _Weather()
    vlm = FakeVLM(VlmReply(status="answered", detail="Paris."))
    await _service(vlm, weather=weather).answer(_query("ask", utterance="what is the capital of france"))
    await _service(vlm, weather=weather).answer(_query())
    assert weather.calls == 0


# ------------------------------------------------------------ phone box (distance)


async def test_a_thing_found_in_the_phone_photo_brings_its_box() -> None:
    reply = _found(6, 0.35, box_2d=[420, 310, 610, 390])
    answer = await _service(FakeVLM(reply)).answer(_query(phone=True))

    assert answer.phone_box is not None
    assert (answer.phone_box.x_min, answer.phone_box.y_min) == (0.31, 0.42)
    assert (answer.phone_box.x_max, answer.phone_box.y_max) == (0.39, 0.61)


async def test_a_box_in_a_360_view_is_dropped_there_is_no_depth_there() -> None:
    answer = await _service(FakeVLM(_found(2, 0.5, box_2d=[100, 100, 300, 300]))).answer(_query(phone=True))
    assert answer.status == "found"
    assert answer.phone_box is None


@pytest.mark.parametrize("raw", [[1, 2, 3], [500, 500, 500, 900], [float("nan"), 0, 10, 10]])
async def test_an_unusable_box_is_ignored_not_fatal(raw: list[float]) -> None:
    answer = await _service(FakeVLM(_found(6, 0.5, box_2d=raw))).answer(_query(phone=True))
    assert answer.status == "found"
    assert answer.phone_box is None


async def test_swapped_corners_are_put_right_and_edges_clamped() -> None:
    answer = await _service(FakeVLM(_found(6, 0.5, box_2d=[900, 1200, 100, 800]))).answer(_query(phone=True))
    assert answer.phone_box is not None
    assert (answer.phone_box.x_min, answer.phone_box.x_max) == (0.8, 1.0)
    assert (answer.phone_box.y_min, answer.phone_box.y_max) == (0.1, 0.9)


async def test_without_x_center_the_box_centre_gives_the_bearing() -> None:
    reply = VlmReply(status="found", image_index=6, subject="sprite can", box_2d=[0, 0, 1000, 500])
    answer = await _service(FakeVLM(reply)).answer(_query(phone=True))
    assert answer.bearing_deg == pytest.approx(-PHONE_HFOV / 4, abs=0.1)


async def test_the_box_centre_gives_the_bearing_over_x_center() -> None:
    # Live: x_center=221 (the box's 0-1000 scale) with box x 107-311 -> the
    # thing is left of centre, not clamped to the right edge.
    reply = _found(6, 221.0, box_2d=[527, 107, 829, 311])
    answer = await _service(FakeVLM(reply)).answer(_query(phone=True))
    assert answer.bearing_deg == pytest.approx((0.209 - 0.5) * PHONE_HFOV, abs=0.1)


async def test_an_x_center_on_the_thousand_scale_is_read_as_a_fraction() -> None:
    answer = await _service(FakeVLM(_found(6, 221.0))).answer(_query(phone=True))
    assert answer.bearing_deg == pytest.approx((0.221 - 0.5) * PHONE_HFOV, abs=0.1)
