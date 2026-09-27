"""The background sweep that fills sign memory, and what it refuses to store.

Two halves. The pure half turns a model's (image_index, x_center) into a
torso-relative bearing with the same geometry the query path uses, and throws
away everything it cannot trust -- a sign with no words, an image index that
does not exist, a reading the model itself called low confidence. The loop
half must never take the wearer's turn: it skips a sweep while a question is
in flight, and a failed sweep is the next sweep's problem rather than the end
of the task.
"""

from __future__ import annotations

import asyncio

import pytest

from cane.scene.place_names import PlaceKind
from cane.scene.places import PlaceMemory
from cane.scene.service import View
from cane.scene.signscan import SignScanner, setting_from, sightings_from
from cane.scene.vlm import FakeVLM, LabeledImage, ScanReply, SignRead, VlmUnavailableError

# Six level crops, as the bridge cuts them: 0 ahead, then every 60 degrees.
YAWS = (0.0, 60.0, 120.0, 180.0, -120.0, -60.0)


def _views() -> tuple[View, ...]:
    return tuple(
        View(image=LabeledImage(f"IMAGE {i}", b"jpeg"), centre_deg=yaw, fov_deg=60.0)
        for i, yaw in enumerate(YAWS)
    )


def _sign(**kwargs: object) -> SignRead:
    base: dict[str, object] = {
        "image_index": 0,
        "x_center": 0.5,
        "text": "RESTROOMS",
        "confidence": "high",
    }
    return SignRead(**{**base, **kwargs})  # type: ignore[arg-type]


# ------------------------------------------------------------------ bearings


@pytest.mark.parametrize(
    ("index", "x_center", "bearing"),
    [
        (0, 0.5, 0.0),  # dead ahead, centre of the forward crop
        (0, 1.0, 30.0),  # right edge of a 60-degree crop
        (0, 0.0, -30.0),  # left edge
        (1, 0.75, 75.0),  # 60 + 0.25 * 60
        (3, 0.5, 180.0),  # the rear crop: directly behind
        (5, 0.5, -60.0),  # over the left shoulder
    ],
)
def test_a_sign_position_becomes_a_torso_relative_bearing(
    index: int, x_center: float, bearing: float
) -> None:
    reply = ScanReply(signs=[_sign(image_index=index, x_center=x_center)], setting="indoor")

    (sighting,) = sightings_from(reply, _views())

    assert sighting.bearing_deg == pytest.approx(bearing)
    assert sighting.text == "RESTROOMS"
    assert sighting.kind is PlaceKind.RESTROOM


def test_every_readable_sign_in_a_sweep_is_returned() -> None:
    reply = ScanReply(
        signs=[
            _sign(image_index=0, x_center=0.5, text="ROOM 100"),
            _sign(image_index=3, x_center=0.5, text="RESTROOMS"),
        ],
        setting="indoor",
    )

    found = sightings_from(reply, _views())

    assert [s.text for s in found] == ["ROOM 100", "RESTROOMS"]
    assert [s.bearing_deg for s in found] == [pytest.approx(0.0), pytest.approx(180.0)]


# ------------------------------------------------------------------ refusals


def test_a_sweep_with_no_signs_stores_nothing() -> None:
    assert sightings_from(ScanReply(signs=[], setting="indoor"), _views()) == ()
    assert sightings_from(ScanReply(signs=None, setting="indoor"), _views()) == ()


@pytest.mark.parametrize("text", [None, "", "   "])
def test_a_sign_with_no_words_is_not_a_place(text: object) -> None:
    reply = ScanReply(signs=[_sign(text=text)], setting="indoor")

    assert sightings_from(reply, _views()) == ()


@pytest.mark.parametrize("index", [None, -1, 6, 99])
def test_a_sign_in_an_image_that_does_not_exist_is_dropped(index: object) -> None:
    """A hallucinated image number has no geometry, so it has no bearing."""
    reply = ScanReply(signs=[_sign(image_index=index)], setting="indoor")

    assert sightings_from(reply, _views()) == ()


def test_a_sign_with_no_position_is_dropped() -> None:
    reply = ScanReply(signs=[_sign(x_center=None)], setting="indoor")

    assert sightings_from(reply, _views()) == ()


def test_a_reading_the_model_itself_calls_low_confidence_is_dropped() -> None:
    """A wrong room number sends the wearer to the wrong door; silence is
    cheaper than a guess."""
    reply = ScanReply(signs=[_sign(confidence="low")], setting="indoor")

    assert sightings_from(reply, _views()) == ()


def test_a_reading_with_no_stated_confidence_is_dropped() -> None:
    reply = ScanReply(signs=[_sign(confidence=None)], setting="indoor")

    assert sightings_from(reply, _views()) == ()


def test_confidence_words_become_scores_that_survive_into_memory() -> None:
    reply = ScanReply(
        signs=[_sign(text="EXIT", confidence="medium"), _sign(text="STAIRS", confidence="high")],
        setting="indoor",
    )

    scores = {s.text: s.confidence for s in sightings_from(reply, _views())}

    assert scores["STAIRS"] > scores["EXIT"]


def test_a_sweep_returning_absurdly_many_signs_is_capped() -> None:
    """Far more signs than a corridor holds means posters and menus are being
    read; the sweep is trimmed rather than trusted wholesale."""
    reply = ScanReply(signs=[_sign(text=f"ROOM {100 + i}") for i in range(40)], setting="indoor")

    assert len(sightings_from(reply, _views())) < 40


# ------------------------------------------------------------------ setting


@pytest.mark.parametrize(
    ("said", "expected"),
    [
        ("indoor", True),
        ("INDOOR", True),
        ("  indoor ", True),
        ("outdoor", False),
        ("Outdoor", False),
        ("maybe", None),  # a model that did not answer the question
        (None, None),
        ("", None),
    ],
)
def test_the_setting_is_read_as_a_yes_no_or_dont_know(said: object, expected: object) -> None:
    assert setting_from(ScanReply(signs=[], setting=said)) is expected  # type: ignore[arg-type]


# ------------------------------------------------------------------ the loop


def _scanner(vlm: FakeVLM, memory: PlaceMemory, **kwargs: object) -> SignScanner:
    views = _views()

    async def view_source() -> tuple[View, ...]:
        return views

    return SignScanner(vlm=vlm, views=view_source, memory=memory, **kwargs)  # type: ignore[arg-type]


async def test_one_sweep_puts_a_sign_into_memory_where_a_question_can_find_it() -> None:
    memory = PlaceMemory()
    vlm = FakeVLM(
        scan_reply=ScanReply(signs=[_sign(image_index=1, x_center=0.75)], setting="indoor")
    )

    await _scanner(vlm, memory).scan_once()

    recalled = memory.recall("the nearest bathroom")
    assert recalled is not None
    assert recalled.bearing_deg == pytest.approx(75.0)
    assert len(vlm.scans) == 1


async def test_the_sweep_asks_for_signage_and_never_for_a_direction_word() -> None:
    memory = PlaceMemory()
    vlm = FakeVLM(scan_reply=ScanReply(signs=[], setting="indoor"))

    await _scanner(vlm, memory).scan_once()

    prompt, images = vlm.scans[0]
    assert "signs" in prompt
    assert "x_center" in prompt
    assert len(images) == 6


async def test_a_sweep_is_skipped_while_the_wearer_has_a_question_in_flight() -> None:
    """The scanner must never compete with the wearer for the model."""
    memory = PlaceMemory()
    vlm = FakeVLM(scan_reply=ScanReply(signs=[_sign()], setting="indoor"))

    await _scanner(vlm, memory, busy=lambda: True).scan_once()

    assert vlm.scans == []
    assert memory.places() == ()


async def test_a_failed_sweep_is_survivable_and_leaves_memory_alone() -> None:
    memory = PlaceMemory()
    vlm = FakeVLM(scan_error=VlmUnavailableError("429 RESOURCE_EXHAUSTED"))

    await _scanner(vlm, memory).scan_once()  # must not raise

    assert memory.places() == ()


async def test_the_scanner_learns_it_is_indoors_from_the_sweep() -> None:
    memory = PlaceMemory()
    scanner = _scanner(FakeVLM(scan_reply=ScanReply(signs=[], setting="indoor")), memory)

    assert scanner.indoor is None  # nothing seen yet, so no claim
    await scanner.scan_once()
    assert scanner.indoor is True


async def test_outdoors_the_sweep_stops_storing_signs() -> None:
    """Outside, the phone's Maps engine owns navigation, so there is nothing
    for sign memory to do and no reason to spend calls filling it."""
    memory = PlaceMemory()
    vlm = FakeVLM(scan_reply=ScanReply(signs=[_sign(text="STARBUCKS")], setting="outdoor"))
    scanner = _scanner(vlm, memory)

    await scanner.scan_once()

    assert scanner.indoor is False
    assert memory.places() == ()


async def test_the_run_loop_keeps_sweeping_until_it_is_cancelled() -> None:
    memory = PlaceMemory()
    vlm = FakeVLM(scan_reply=ScanReply(signs=[_sign()], setting="indoor"))
    scanner = _scanner(vlm, memory, interval_s=0.001)

    task = asyncio.create_task(scanner.run())
    for _ in range(200):
        await asyncio.sleep(0.001)
        if len(vlm.scans) >= 3:
            break
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert len(vlm.scans) >= 3
