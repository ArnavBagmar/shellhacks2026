"""Step 26 -- the steering evidence as the voice layer sees it.

Three things are pinned here and nowhere else:

1. the reading the wearer is told about is the *same* one the haptic cue was
   rendered from (it arrives as the ``steer`` frame the phone already got),
   so "the left is clear" can never be spoken while the cue steers right;
2. a reading expires, so a bridge that stopped sending ``steer`` (local
   fallback, plan Sec 4.2) goes quiet instead of quoting a stale ring;
3. no wording this module can produce states a distance -- ``anchored`` is
   false in the field (plan Sec 2.5) and a scale-free ring knows *which way
   is freer*, never metres (Sec 3.3 ruling 3).
"""

from __future__ import annotations

import re

import pytest

from cane.scene.gap import (
    CLEAR_WAY_MAX_AGE_MS,
    ClearWay,
    LatestClearWay,
    SteerTap,
)
from cane.scene.gap_speech import (
    NO_CLEAR_PATH,
    clear_way_fragment,
    clear_way_sentence,
    sector_phrase,
)
from cane.transport.messages import STEER_TTL_MS, Steer

T_CAPTURE_MS = 1_790_000_000_000
#: A distance claim in any unit the wearer would hear as metres.
_METRIC = re.compile(r"\b\d|\b(m|metre|metres|meter|meters|ft|foot|feet|step|steps)\b")


def _steer(**fields: object) -> Steer:
    values: dict[str, object] = {
        "t_capture_ms": T_CAPTURE_MS,
        "ttl_ms": STEER_TTL_MS,
        "steer": 0.35,
        "proximity": 0.5,
        "gap_bearing_deg": 45.0,
        "blocked": False,
        "proximity_anchored": False,
        "source": "x4_cloud",
    }
    values.update(fields)
    return Steer.model_validate(values)


class _Clock:
    """A hand-cranked monotonic clock in seconds."""

    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


class _RecordingSink:
    def __init__(self, delivered: bool = True) -> None:
        self.delivered = delivered
        self.sent: list[Steer] = []

    async def send_steer(self, steer: Steer) -> bool:
        self.sent.append(steer)
        return self.delivered


# ------------------------------------------------------------------ reading


def test_a_reading_is_exactly_what_the_steer_frame_said() -> None:
    way = ClearWay.from_steer(_steer(gap_bearing_deg=-62.5, blocked=True))

    assert way == ClearWay(bearing_deg=-62.5, blocked=True, anchored=False)


def test_the_anchor_flag_travels_so_the_words_can_stay_honest() -> None:
    way = ClearWay.from_steer(_steer(proximity_anchored=True))

    assert way.anchored is True


# -------------------------------------------------------------------- store


def test_the_store_hands_back_the_freshest_reading() -> None:
    store = LatestClearWay(monotonic=_Clock())
    store.publish(ClearWay(bearing_deg=10.0, blocked=False, anchored=False))
    store.publish(ClearWay(bearing_deg=-70.0, blocked=True, anchored=False))

    latest = store.latest_clear_way()

    assert latest is not None and latest.bearing_deg == -70.0


def test_an_empty_store_has_nothing_to_say() -> None:
    assert LatestClearWay(monotonic=_Clock()).latest_clear_way() is None


def test_a_reading_expires_rather_than_going_stale_in_silence() -> None:
    clock = _Clock()
    store = LatestClearWay(monotonic=clock)
    store.publish(ClearWay(bearing_deg=45.0, blocked=False, anchored=False))

    clock.now += (CLEAR_WAY_MAX_AGE_MS - 1) / 1000.0
    assert store.latest_clear_way() is not None

    clock.now += 2 / 1000.0
    assert store.latest_clear_way() is None


def test_the_max_age_is_derived_from_the_steer_ttl_not_invented() -> None:
    assert CLEAR_WAY_MAX_AGE_MS % STEER_TTL_MS == 0
    # Long enough to survive a settle plus a 3 Hz tick, short enough that a
    # bridge which flipped to local goes quiet inside one spoken answer.
    assert 1000 <= CLEAR_WAY_MAX_AGE_MS <= 3000


# ---------------------------------------------------------------------- tap


async def test_the_tap_publishes_and_still_forwards_the_frame() -> None:
    store = LatestClearWay(monotonic=_Clock())
    sink = _RecordingSink()
    frame = _steer(gap_bearing_deg=88.125, blocked=True)

    assert await SteerTap(sink, store).send_steer(frame) is True

    assert sink.sent == [frame]
    assert store.latest_clear_way() == ClearWay(
        bearing_deg=88.125, blocked=True, anchored=False
    )


async def test_the_bridge_taps_the_steer_stream_only_when_the_voice_layer_is_on() -> None:
    from cane.bridge.__main__ import build_steer_sink

    sink = _RecordingSink()
    store = LatestClearWay(monotonic=_Clock())

    assert build_steer_sink(sink, None) is sink

    tapped = build_steer_sink(sink, store)
    assert tapped is not sink
    await tapped.send_steer(_steer(gap_bearing_deg=-45.0))
    assert store.latest_clear_way() is not None
    assert len(sink.sent) == 1


async def test_the_tap_publishes_even_when_the_phone_did_not_take_it() -> None:
    """The ring was measured whether or not a phone was listening, and the
    wearer is asking the laptop, not the socket.
    """
    store = LatestClearWay(monotonic=_Clock())
    sink = _RecordingSink(delivered=False)

    assert await SteerTap(sink, store).send_steer(_steer()) is False
    assert store.latest_clear_way() is not None


# ------------------------------------------------------------------- words


@pytest.mark.parametrize(
    ("bearing", "phrase"),
    [
        (0.0, "straight ahead"),
        (14.9, "straight ahead"),
        (-14.9, "straight ahead"),
        (15.0, "ahead to the right"),
        (-15.0, "ahead to the left"),
        (45.0, "ahead to the right"),
        (-59.9, "ahead to the left"),
        (60.0, "to your right"),
        (-88.125, "to your left"),
        (90.0, "to your right"),
    ],
)
def test_the_side_words_are_the_ones_hazards_already_use(
    bearing: float, phrase: str
) -> None:
    assert sector_phrase(bearing) == phrase


def test_a_clear_right_side_is_named_as_more_open_not_as_a_distance() -> None:
    way = ClearWay(bearing_deg=45.0, blocked=False, anchored=False)

    assert clear_way_fragment(way) == "most open ahead to the right"
    assert clear_way_sentence(way) == "Most open ahead to the right."


def test_the_mirrored_reading_names_the_left() -> None:
    way = ClearWay(bearing_deg=-45.0, blocked=False, anchored=False)

    assert clear_way_sentence(way) == "Most open ahead to the left."


def test_blocked_is_announced_and_names_the_passable_side() -> None:
    way = ClearWay(bearing_deg=88.125, blocked=True, anchored=False)

    assert clear_way_fragment(way) == "no clear path ahead, most open to your right"
    assert clear_way_sentence(way) == "No clear path ahead, most open to your right."


def test_a_structureless_ring_is_blocked_with_no_side_claimed() -> None:
    """Plan Sec 3.3 ruling 3: a flat ring reports ``blocked`` and its
    least-bad column is dead ahead, which is no side at all. "no clear path
    ahead, most open straight ahead" would be a contradiction spoken aloud.
    """
    way = ClearWay(bearing_deg=1.875, blocked=True, anchored=False)

    assert clear_way_fragment(way) == NO_CLEAR_PATH
    assert clear_way_sentence(way) == "No clear path ahead."


@pytest.mark.parametrize("blocked", [False, True])
@pytest.mark.parametrize("anchored", [False, True])
def test_no_wording_this_module_can_produce_states_a_distance(
    blocked: bool, anchored: bool
) -> None:
    for bearing in range(-90, 91):
        way = ClearWay(bearing_deg=float(bearing), blocked=blocked, anchored=anchored)
        for words in (clear_way_fragment(way), clear_way_sentence(way)):
            assert not _METRIC.search(words), words


def test_the_words_do_not_change_when_the_anchor_is_accepted() -> None:
    """A guard, not a feature: if a later step turns metres on, it has to come
    through here and change this test deliberately (plan Sec 2.5, step 27).
    """
    unanchored = ClearWay(bearing_deg=70.0, blocked=False, anchored=False)
    anchored = ClearWay(bearing_deg=70.0, blocked=False, anchored=True)

    assert clear_way_sentence(anchored) == clear_way_sentence(unanchored)
