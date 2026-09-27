"""Answering "where is the restroom" from memory instead of from the frame.

The rule being pinned here is about *when* memory is allowed to answer. A
sighting fresh enough to still carry a bearing answers on its own and costs no
model call at all -- that is the whole point of scanning in the background. A
sighting too old for its angle does **not** short-circuit: the ring is asked
first, because the sign may be in view right now, and memory only speaks if
the live look found nothing.
"""

from __future__ import annotations

import numpy as np
import pytest

from cane.scene.frames import StillProvider
from cane.scene.places import Recall, Sighting
from cane.scene.service import SceneQueryService
from cane.scene.vlm import FakeVLM, VlmReply
from cane.transport.messages import SceneQuery

EQUIRECT = np.zeros((180, 360, 3), dtype=np.uint8)


class StubPlaces:
    """A memory with one prepared answer, recording what it was asked."""

    def __init__(self, recall: Recall | None) -> None:
        self._recall = recall
        self.asked: list[str] = []

    def recall(self, target: str) -> Recall | None:
        self.asked.append(target)
        return self._recall


def _query(kind: str = "find", target: str | None = "bathroom") -> SceneQuery:
    return SceneQuery(
        query_id="q-1",
        kind=kind,  # type: ignore[arg-type]
        target=target if kind == "find" else None,
        utterance="where is the nearest bathroom",
        t_phone_ms=1_790_000_000_000,
        phone_frame=None,
    )


def _recall(*, age_ms: int, bearing: float | None, text: str = "RESTROOMS") -> Recall:
    return Recall(
        sighting=Sighting.seen(text=text, bearing_deg=bearing or 0.0, confidence=0.9),
        age_ms=age_ms,
        bearing_deg=bearing,
    )


def _service(vlm: FakeVLM, places: StubPlaces | None) -> SceneQueryService:
    return SceneQueryService(
        vlm,  # type: ignore[arg-type]
        StillProvider(EQUIRECT),
        settle_s=0.0,
        crop_px=32,
        places=places,  # type: ignore[arg-type]
    )


# ------------------------------------------------------------------ fresh


async def test_a_fresh_sighting_answers_without_asking_the_model_at_all() -> None:
    """The scanner already paid for this; asking again would cost the wearer
    a second or two for an answer memory already has."""
    vlm = FakeVLM(VlmReply(status="not_found"))
    places = StubPlaces(_recall(age_ms=2_000, bearing=72.5))

    answer = await _service(vlm, places).answer(_query())

    assert answer.status == "found"
    assert answer.bearing_deg == pytest.approx(72.5)
    assert vlm.calls == []  # not one model call
    assert places.asked == ["bathroom"]


async def test_a_remembered_sign_is_named_in_words_a_voice_can_say() -> None:
    vlm = FakeVLM(VlmReply(status="not_found"))
    places = StubPlaces(_recall(age_ms=2_000, bearing=10.0, text="RESTROOMS"))

    answer = await _service(vlm, places).answer(_query())

    assert answer.subject == "restroom sign"  # not the shouted plate


async def test_a_sighting_seen_moments_ago_needs_no_age_clause() -> None:
    vlm = FakeVLM(VlmReply(status="not_found"))
    places = StubPlaces(_recall(age_ms=1_000, bearing=10.0))

    answer = await _service(vlm, places).answer(_query())

    assert answer.detail is None


async def test_a_sighting_from_a_while_back_says_when_it_was_seen() -> None:
    vlm = FakeVLM(VlmReply(status="not_found"))
    places = StubPlaces(_recall(age_ms=15_000, bearing=170.0))

    answer = await _service(vlm, places).answer(_query())

    assert answer.status == "found"
    assert answer.bearing_deg == pytest.approx(170.0)
    assert answer.detail is not None
    assert "seen" in answer.detail


# ------------------------------------------------------------------ stale


async def test_a_sighting_too_old_to_point_at_lets_the_ring_look_first() -> None:
    """The sign might be in view right now; a stale memory must not pre-empt
    a live look that could give a real bearing."""
    vlm = FakeVLM(VlmReply(status="found", image_index=0, x_center=0.5, subject="restroom sign"))
    places = StubPlaces(_recall(age_ms=120_000, bearing=None))

    answer = await _service(vlm, places).answer(_query())

    assert len(vlm.calls) == 1
    assert answer.status == "found"
    assert answer.bearing_deg is not None  # the live look's bearing, not memory's


async def test_when_the_ring_sees_nothing_a_stale_memory_still_answers() -> None:
    """This is the recall beat: the wearer walked past it, it is out of view,
    and "I don't see any" would be a worse answer than "you passed one"."""
    vlm = FakeVLM(VlmReply(status="not_found"))
    places = StubPlaces(_recall(age_ms=120_000, bearing=None))

    answer = await _service(vlm, places).answer(_query())

    assert answer.status == "found"
    assert answer.subject == "restroom sign"
    assert answer.bearing_deg is None  # no angle is claimed
    assert answer.detail is not None and "seen" in answer.detail
    assert answer.confidence == "low"  # the phone prefixes "Maybe"


async def test_nothing_remembered_and_nothing_seen_stays_not_found() -> None:
    vlm = FakeVLM(VlmReply(status="not_found"))
    places = StubPlaces(None)

    answer = await _service(vlm, places).answer(_query())

    assert answer.status == "not_found"
    assert answer.bearing_deg is None


# ------------------------------------------------------------------ scope


async def test_memory_is_never_consulted_for_a_question_that_is_not_a_find() -> None:
    vlm = FakeVLM(VlmReply(status="described", detail="a corridor"))
    places = StubPlaces(_recall(age_ms=1_000, bearing=10.0))

    answer = await _service(vlm, places).answer(_query(kind="describe_around"))

    assert places.asked == []
    assert answer.status == "described"


async def test_a_service_with_no_memory_behaves_exactly_as_before() -> None:
    vlm = FakeVLM(VlmReply(status="not_found"))

    answer = await _service(vlm, None).answer(_query())

    assert answer.status == "not_found"
    assert len(vlm.calls) == 1
