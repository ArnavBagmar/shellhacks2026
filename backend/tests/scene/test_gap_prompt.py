"""Step 26 -- the ring in the prompt, and in the answer the wearer hears.

The prompt half keeps ``prompt.py``'s load-bearing principle intact:
**direction never travels as words between us and the model.** The model
already answers with an image index and a horizontal position; here the
service *asks* in the same vocabulary, so a ring bearing reaches Gemini as
pixels and never as "left". The side the wearer is told is computed by our
own geometry, exactly as ``bearing_at`` computes a found subject's bearing.

The end-to-end half drives real fixture rings through the real bridge entry
point (``steer_from_response``) so the answer is provably agreeing with the
steering law rather than with a second opinion written in this package.
"""

from __future__ import annotations

import base64
import re

import numpy as np
import pytest

from cane.bridge.dewarp import CROP_YAWS_DEG
from cane.bridge.steer_frame import steer_from_response
from cane.cloud.api_models import COLUMNS_PER_CROP, GapProfile, InferenceResponse
from cane.scene.frames import StillProvider
from cane.scene.gap import CLEAR_WAY_MAX_AGE_MS, ClearWay, LatestClearWay
from cane.scene.prompt import (
    MAX_GAP_CLAUSE_CHARS,
    MAX_GAP_CLAUSE_NUMBERS,
    GapHint,
    build_prompt,
    gap_clause,
)
from cane.scene.service import SceneQueryService, View, gap_hint_for
from cane.scene.vlm import FakeVLM, LabeledImage, VlmReply
from cane.transport.messages import PhoneFrame, SceneQuery

EQUIRECT = np.zeros((180, 360, 3), dtype=np.uint8)
CROP_PX = 32
PHONE_HFOV = 70.0
TINY_JPEG_B64 = base64.b64encode(b"\xff\xd8\xff\xe0 not really a jpeg").decode("ascii")
T_CAPTURE_MS = 1_790_000_000_000
#: Raw relative depth is inverse depth: larger means nearer (gap_ring.py).
NEAR, FAR = 3.0, 1.0

#: Byte-for-byte what step 20 shipped. Asserted, not derived, so this step
#: cannot quietly reword the prompt that is known to work.
DESCRIBE_FRONT_PROMPT_TODAY = (
    "You are the eyes of a blind pedestrian who is standing still. The images "
    "above show their surroundings; image-left is the wearer's left. They asked: "
    '"what is in front of me". Describe what is in front of them in at most 30 '
    "words, most useful for walking first: paths, doors, stairs, obstacles, "
    'signs, people. Answer with JSON: status "described"; detail = the '
    "description; everything else null. Do not guess at things you cannot see."
)


def _query(kind: str = "describe_front", *, phone: bool = False) -> SceneQuery:
    utterance = {
        "describe_front": "what is in front of me",
        "describe_around": "what is around me",
        "find": "is there a bathroom around",
    }[kind]
    return SceneQuery(
        query_id="q-1",
        kind=kind,  # type: ignore[arg-type]
        target="bathroom" if kind == "find" else None,
        utterance=utterance,
        t_phone_ms=T_CAPTURE_MS,
        phone_frame=PhoneFrame(jpeg_b64=TINY_JPEG_B64, hfov_deg=PHONE_HFOV, width=4, height=3)
        if phone
        else None,
    )


def _service(vlm: FakeVLM, ways: object = None, **kwargs: object) -> SceneQueryService:
    options: dict[str, object] = {"settle_s": 0.0, "crop_px": CROP_PX, **kwargs}
    return SceneQueryService(vlm, StillProvider(EQUIRECT), clear_ways=ways, **options)  # type: ignore[arg-type]


class _Clock:
    """A hand-cranked monotonic clock in seconds."""

    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


def _store(way: ClearWay) -> LatestClearWay:
    store = LatestClearWay()
    store.publish(way)
    return store


# ------------------------------------------------------------ fixture rings


def _profile(crop_index: int, columns: tuple[float, ...]) -> GapProfile:
    return GapProfile(
        crop_index=crop_index,
        columns=columns,
        band_rows=(0, 10),
        ground_fit_residual=0.9,
        anchored=False,
    )


def _ring_reading(columns_by_crop: dict[int, tuple[float, ...]]) -> ClearWay:
    """A fixture ring -> the reading the voice layer gets live.

    Deliberately routed through ``steer_from_response``: the bridge's own
    entry point, the same call the pipeline makes, so the side named below is
    the side the haptic cue was rendered from.
    """
    response = InferenceResponse.model_validate(
        {
            "schema": 1,
            "t_capture_ms": T_CAPTURE_MS,
            "inference_ms": 12.0,
            "detections": (),
            "gap_profiles": tuple(
                _profile(index, columns) for index, columns in columns_by_crop.items()
            ),
        }
    )
    steer = steer_from_response(response, CROP_YAWS_DEG, 0.0)
    assert steer is not None
    return ClearWay.from_steer(steer)


_WALL = (NEAR,) * COLUMNS_PER_CROP
_OPEN = (FAR,) * COLUMNS_PER_CROP
_NEAR_THEN_FAR = tuple(
    NEAR if index < COLUMNS_PER_CROP // 2 else FAR for index in range(COLUMNS_PER_CROP)
)
#: Too narrow to clear the body-width test, so the ring is ``blocked`` while
#: still knowing which way is least bad (plan Sec 3.1 ruling 2).
_RIGHT_NOTCH = tuple(
    FAR if index >= COLUMNS_PER_CROP - 2 else NEAR for index in range(COLUMNS_PER_CROP)
)


def _left_blocked_ring() -> ClearWay:
    return _ring_reading({5: _WALL, 0: _NEAR_THEN_FAR, 1: _OPEN})


def _right_blocked_ring() -> ClearWay:
    return _ring_reading({5: _OPEN, 0: tuple(reversed(_NEAR_THEN_FAR)), 1: _WALL})


# --------------------------------------------------------- prompt geometry


def test_a_bearing_becomes_an_image_and_an_x_the_model_already_understands() -> None:
    view = View(image=LabeledImage("x", b""), centre_deg=60.0, fov_deg=60.0)

    assert view.x_for_bearing(60.0) == pytest.approx(0.5)
    assert view.x_for_bearing(90.0) == pytest.approx(1.0)
    assert view.x_for_bearing(30.0) == pytest.approx(0.0)
    assert view.x_for_bearing(0.0) is None


def test_x_for_bearing_is_the_exact_inverse_of_bearing_at() -> None:
    for centre in (-120.0, -60.0, 0.0, 60.0, 180.0):
        view = View(image=LabeledImage("x", b""), centre_deg=centre, fov_deg=60.0)
        for x in (0.0, 0.17, 0.5, 0.83, 1.0):
            back = view.x_for_bearing(view.bearing_at(x))
            assert back == pytest.approx(x, abs=1e-3)


def test_the_hint_picks_the_image_that_holds_the_gap_most_centrally() -> None:
    views = [
        View(image=LabeledImage("a", b""), centre_deg=0.0, fov_deg=60.0),
        View(image=LabeledImage("b", b""), centre_deg=60.0, fov_deg=60.0),
    ]
    way = ClearWay(bearing_deg=29.0, blocked=False, anchored=False)

    hint = gap_hint_for(way, views)

    # 29 deg lands at x=0.98 in the forward view and just off the left edge
    # of the +60 one, so only the forward view can hold it at all.
    assert hint == GapHint(image_index=0, x_center=0.98, blocked=False)


def test_a_gap_no_image_can_see_is_said_to_be_off_image_not_guessed_at() -> None:
    views = [View(image=LabeledImage("phone", b""), centre_deg=0.0, fov_deg=PHONE_HFOV)]
    way = ClearWay(bearing_deg=88.125, blocked=True, anchored=False)

    hint = gap_hint_for(way, views)

    assert hint == GapHint(image_index=None, x_center=None, blocked=True)


def test_no_reading_means_no_hint() -> None:
    assert gap_hint_for(None, []) is None


def test_a_hint_must_carry_an_image_and_an_x_together_or_neither() -> None:
    with pytest.raises(ValueError):
        GapHint(image_index=1, x_center=None, blocked=False)


# ------------------------------------------------------------ prompt growth


def test_without_a_ring_the_prompt_is_byte_identical_to_step_20s() -> None:
    assert build_prompt(_query()) == DESCRIBE_FRONT_PROMPT_TODAY
    assert build_prompt(_query(), None) == DESCRIBE_FRONT_PROMPT_TODAY


def test_the_ring_clause_is_appended_and_leaves_the_old_prompt_untouched() -> None:
    hint = GapHint(image_index=2, x_center=0.83, blocked=False)

    grown = build_prompt(_query(), hint)

    assert grown.startswith(DESCRIBE_FRONT_PROMPT_TODAY)
    assert grown[len(DESCRIBE_FRONT_PROMPT_TODAY) :] == gap_clause(hint)


def test_the_clause_never_speaks_a_direction_to_the_model() -> None:
    """``prompt.py``'s design principle: the model is pointed at pixels, so it
    cannot echo a side word we did not compute geometrically.
    """
    for blocked in (False, True):
        clause = gap_clause(GapHint(image_index=1, x_center=0.97, blocked=blocked))
        for word in (" left ", " right ", "leftmost", "rightmost", "behind"):
            assert word not in clause.replace("left edge", "").replace("right edge", "")


@pytest.mark.parametrize("blocked", [False, True])
@pytest.mark.parametrize(
    "hint_fields", [(9, 0.97), (0, 0.0), (5, 1.0), (None, None)]
)
def test_the_clause_is_bounded_in_size_and_in_numbers(
    hint_fields: tuple[int | None, float | None], blocked: bool
) -> None:
    index, x = hint_fields
    clause = gap_clause(GapHint(image_index=index, x_center=x, blocked=blocked))

    assert len(clause) <= MAX_GAP_CLAUSE_CHARS
    # Two numbers of evidence plus the 0/1 edge convention -- never 48 floats.
    assert len(re.findall(r"\d+(?:\.\d+)?", clause)) <= MAX_GAP_CLAUSE_NUMBERS


def test_the_bound_is_small_enough_to_be_free_beside_six_jpegs() -> None:
    assert MAX_GAP_CLAUSE_CHARS <= 280


@pytest.mark.parametrize("kind", ["find", "describe_around", "describe_front"])
def test_every_kind_grows_by_at_most_the_bound(kind: str) -> None:
    hint = GapHint(image_index=1, x_center=0.97, blocked=True)
    query = _query(kind)

    growth = len(build_prompt(query, hint)) - len(build_prompt(query))

    assert 0 < growth <= MAX_GAP_CLAUSE_CHARS


# -------------------------------------------------- the answer the wearer hears


async def test_a_left_blocked_ring_names_the_right_side_as_the_clear_one() -> None:
    vlm = FakeVLM(VlmReply(status="described", detail="a corridor and a bench"))

    answer = await _service(vlm, _store(_left_blocked_ring())).answer(_query())

    assert answer.status == "described"
    assert answer.detail == "Most open ahead to the right. a corridor and a bench"


async def test_the_mirrored_ring_names_the_left() -> None:
    vlm = FakeVLM(VlmReply(status="described", detail="a corridor and a bench"))

    answer = await _service(vlm, _store(_right_blocked_ring())).answer(_query())

    assert answer.detail == "Most open ahead to the left. a corridor and a bench"


async def test_the_prompt_points_gemini_at_the_same_gap_it_will_be_told_about() -> None:
    vlm = FakeVLM(VlmReply(status="described", detail="a corridor"))

    await _service(vlm, _store(_left_blocked_ring())).answer(_query())

    (prompt, images), = vlm.calls
    # The +45 deg gap is dead centre of the third forward crop (centre +60 is
    # image 2 of C5/C0/C1)... at x = 0.25 of it.
    assert "image 2 at horizontal position 0.25" in prompt
    assert len(images) == 3


async def test_a_blocked_ring_is_announced_on_a_description() -> None:
    way = _ring_reading({5: _WALL, 0: _WALL, 1: _RIGHT_NOTCH})
    assert way.blocked is True
    vlm = FakeVLM(VlmReply(status="described", detail="a wall and a bin"))

    answer = await _service(vlm, _store(way)).answer(_query())

    assert answer.detail == (
        "No clear path ahead, most open to your right. a wall and a bin"
    )


async def test_a_blocked_ring_plus_a_route_cue_names_the_passable_side() -> None:
    """The one place a wearer can learn ``blocked`` (plan Sec 3.2 ruling 4),
    riding the answer that is itself a turn cue: "To your right, restroom
    sign, no clear path ahead, most open to your right."
    """
    way = _ring_reading({5: _WALL, 0: _WALL, 1: _RIGHT_NOTCH})
    reply = VlmReply(
        status="found", image_index=1, x_center=0.5, subject="restroom sign",
        detail="past the elevators", confidence="medium",
    )

    answer = await _service(FakeVLM(reply), _store(way)).answer(_query("find"))

    assert answer.status == "found"
    assert answer.bearing_deg == pytest.approx(60.0)
    assert answer.detail == (
        "no clear path ahead, most open to your right; past the elevators"
    )


async def test_an_unblocked_ring_stays_out_of_a_found_answers_directions() -> None:
    """A ``found`` answer already carries one bearing. A second, unrelated
    direction in the same breath is the confusion this step exists to stop.
    """
    reply = VlmReply(
        status="found", image_index=1, x_center=0.5, subject="restroom sign",
        detail="past the elevators", confidence="medium",
    )

    answer = await _service(FakeVLM(reply), _store(_left_blocked_ring())).answer(
        _query("find")
    )

    assert answer.detail == "past the elevators"


async def test_the_clause_survives_a_model_that_overruns_the_detail_cap() -> None:
    long_detail = "corridor " * 60
    vlm = FakeVLM(VlmReply(status="described", detail=long_detail))

    answer = await _service(vlm, _store(_left_blocked_ring())).answer(_query())

    assert answer.detail is not None
    assert answer.detail.startswith("Most open ahead to the right. corridor")
    assert len(answer.detail) == 240


async def test_a_ring_never_rescues_an_answer_the_model_could_not_give() -> None:
    """``unavailable`` means "we could not look". A depth ring is not a
    description, and dressing one up as ``described`` would tell the wearer
    the question was answered when it was not.
    """
    vlm = FakeVLM(VlmReply(status="described", detail="null"))

    answer = await _service(vlm, _store(_left_blocked_ring())).answer(_query())

    assert answer.status == "unavailable"
    assert answer.detail is None


async def test_no_answer_states_metres_while_the_ring_is_unanchored() -> None:
    metric = re.compile(r"\b\d|\b(m|metre|metres|meter|meters|ft|foot|feet)\b")
    vlm = FakeVLM(VlmReply(status="described", detail="a corridor"))

    for way in (_left_blocked_ring(), _right_blocked_ring()):
        assert way.anchored is False
        answer = await _service(vlm, _store(way)).answer(_query())
        assert answer.detail is not None
        head = answer.detail.split(". ")[0]
        assert not metric.search(head), head


# ------------------------------------------------------- nothing regresses


async def test_with_no_ring_the_answer_still_returns_unchanged() -> None:
    vlm = FakeVLM(VlmReply(status="described", detail="a corridor and a bench"))

    answer = await _service(vlm, None).answer(_query())

    assert answer.status == "described"
    assert answer.detail == "a corridor and a bench"
    (prompt, _), = vlm.calls
    assert prompt == DESCRIBE_FRONT_PROMPT_TODAY


async def test_an_expired_reading_is_the_same_as_no_reading() -> None:
    """The bridge stops sending ``steer`` in local-fallback mode (plan
    Sec 4.2), so the voice layer must fall silent about the ring rather than
    quote the last one it saw.
    """
    clock = _Clock()
    store = LatestClearWay(monotonic=clock)
    store.publish(_left_blocked_ring())
    clock.now += CLEAR_WAY_MAX_AGE_MS / 1000.0 + 1.0
    vlm = FakeVLM(VlmReply(status="described", detail="a corridor"))

    answer = await _service(vlm, store).answer(_query())

    assert answer.detail == "a corridor"
    (prompt, _), = vlm.calls
    assert prompt == DESCRIBE_FRONT_PROMPT_TODAY


async def test_describe_around_and_find_are_untouched_without_a_ring() -> None:
    described = await _service(
        FakeVLM(VlmReply(status="described", detail="a hall")), None
    ).answer(_query("describe_around"))
    assert described.detail == "a hall"

    reply = VlmReply(
        status="found", image_index=1, x_center=0.5, subject="restroom sign",
        detail="past the elevators", confidence="medium",
    )
    found = await _service(FakeVLM(reply), None).answer(_query("find"))
    assert found.detail == "past the elevators"
    assert found.bearing_deg == pytest.approx(60.0)


async def test_a_gap_outside_the_only_image_is_still_announced_to_the_wearer() -> None:
    """describe_front with a phone photo sends the phone's ~70 deg view only,
    so a gap at +88 is off-image for Gemini -- but the wearer still needs it.
    """
    way = _ring_reading({5: _WALL, 0: _WALL, 1: _RIGHT_NOTCH})
    vlm = FakeVLM(VlmReply(status="described", detail="a wall"))

    answer = await _service(vlm, _store(way)).answer(_query(phone=True))

    (prompt, images), = vlm.calls
    assert len(images) == 1
    assert "outside these images" in prompt
    assert answer.detail is not None
    assert answer.detail.startswith("No clear path ahead, most open to your right.")
