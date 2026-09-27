"""The in-session memory of signs the ring has read.

The behaviour that matters is what happens as a sighting *ages*. A sign just
seen can be pointed at. A sign seen a while ago is still worth telling the
wearer about, but its angle is no longer evidence -- there is no odometry in
this system, so the stored bearing is only where the sign *was*. The store's
job is to keep that distinction, and a fake clock is how it gets tested.
"""

from __future__ import annotations

import pytest

from cane.scene.place_names import PlaceKind
from cane.scene.places import (
    BEARING_TRUST_MAX_AGE_MS,
    PLACE_MAX_AGE_MS,
    PlaceMemory,
    Sighting,
)


class FakeClock:
    """A monotonic clock the test advances by hand."""

    def __init__(self) -> None:
        self.seconds = 1000.0

    def __call__(self) -> float:
        return self.seconds

    def advance_ms(self, ms: float) -> None:
        self.seconds += ms / 1000.0


def _memory(clock: FakeClock) -> PlaceMemory:
    return PlaceMemory(monotonic=clock)


def _sign(text: str = "RESTROOMS", bearing: float = 72.5, confidence: float = 0.9) -> Sighting:
    return Sighting.seen(text=text, bearing_deg=bearing, confidence=confidence)


# ------------------------------------------------------------------ recall


def test_an_empty_memory_recalls_nothing() -> None:
    assert _memory(FakeClock()).recall("restroom") is None


def test_a_sign_just_seen_is_recalled_with_its_bearing() -> None:
    clock = FakeClock()
    memory = _memory(clock)
    memory.publish(_sign())

    recalled = memory.recall("the nearest bathroom")

    assert recalled is not None
    assert recalled.sighting.text == "RESTROOMS"
    assert recalled.sighting.kind is PlaceKind.RESTROOM
    assert recalled.bearing_deg == pytest.approx(72.5)
    assert recalled.age_ms == 0


def test_an_unrelated_target_recalls_nothing_even_when_memory_is_full() -> None:
    memory = _memory(FakeClock())
    memory.publish(_sign())
    memory.publish(_sign(text="EXIT"))

    assert memory.recall("starbucks") is None


def test_the_classified_kind_is_stored_not_recomputed_by_the_caller() -> None:
    memory = _memory(FakeClock())
    memory.publish(_sign(text="ROOMS 100-110"))

    recalled = memory.recall("room 104")

    assert recalled is not None
    assert recalled.sighting.kind is PlaceKind.ROOM


# ------------------------------------------------------------------ ageing


def test_a_sighting_older_than_the_bearing_window_is_recalled_without_an_angle() -> None:
    """The sign is still worth mentioning; the angle is no longer evidence."""
    clock = FakeClock()
    memory = _memory(clock)
    memory.publish(_sign())

    clock.advance_ms(BEARING_TRUST_MAX_AGE_MS + 1)
    recalled = memory.recall("restroom")

    assert recalled is not None
    assert recalled.bearing_deg is None
    assert recalled.sighting.bearing_deg == pytest.approx(72.5)  # still on the record
    assert recalled.age_ms == BEARING_TRUST_MAX_AGE_MS + 1


def test_a_sighting_inside_the_bearing_window_keeps_its_angle() -> None:
    clock = FakeClock()
    memory = _memory(clock)
    memory.publish(_sign())

    clock.advance_ms(BEARING_TRUST_MAX_AGE_MS - 1)
    recalled = memory.recall("restroom")

    assert recalled is not None
    assert recalled.bearing_deg == pytest.approx(72.5)


def test_a_sighting_older_than_the_memory_window_is_forgotten_entirely() -> None:
    clock = FakeClock()
    memory = _memory(clock)
    memory.publish(_sign())

    clock.advance_ms(PLACE_MAX_AGE_MS + 1)

    assert memory.recall("restroom") is None
    assert memory.places() == ()


# ------------------------------------------------------------------ merging


def test_seeing_the_same_sign_again_moves_its_bearing_and_counts_the_sighting() -> None:
    """This is how a sign the wearer walks past migrates rearward on its own:
    each scan re-publishes it at the angle it is now seen at."""
    clock = FakeClock()
    memory = _memory(clock)
    memory.publish(_sign(bearing=20.0))
    clock.advance_ms(3000)
    memory.publish(_sign(bearing=95.0))
    clock.advance_ms(3000)
    memory.publish(_sign(bearing=170.0))

    recalled = memory.recall("restroom")

    assert recalled is not None
    assert recalled.bearing_deg == pytest.approx(170.0)
    assert recalled.sighting.seen_count == 3
    assert recalled.age_ms == 0  # the age is of the latest sighting, not the first
    assert len(memory.places()) == 1  # one place, not three


def test_two_different_rooms_do_not_collide() -> None:
    memory = _memory(FakeClock())
    memory.publish(_sign(text="ROOM 100", bearing=-30.0))
    memory.publish(_sign(text="ROOM 101", bearing=40.0))

    first = memory.recall("room 100")
    second = memory.recall("room 101")

    assert first is not None and first.bearing_deg == pytest.approx(-30.0)
    assert second is not None and second.bearing_deg == pytest.approx(40.0)
    assert len(memory.places()) == 2


def test_when_two_signs_match_the_freshest_one_answers() -> None:
    clock = FakeClock()
    memory = _memory(clock)
    memory.publish(_sign(text="RESTROOMS", bearing=-90.0))
    clock.advance_ms(5000)
    memory.publish(_sign(text="TOILETS", bearing=10.0))

    recalled = memory.recall("bathroom")

    assert recalled is not None
    assert recalled.sighting.text == "TOILETS"
    assert recalled.bearing_deg == pytest.approx(10.0)


# ------------------------------------------------------------------ bearings


@pytest.mark.parametrize(
    ("published", "stored"),
    [
        (190.0, -170.0),  # folded into the wire's half-open range
        (-180.0, 180.0),  # the contract's range is (-180, 180]
        (360.0, 0.0),
        (72.5, 72.5),
    ],
)
def test_a_published_bearing_is_folded_into_the_wire_range(published: float, stored: float) -> None:
    memory = _memory(FakeClock())
    memory.publish(_sign(bearing=published))

    recalled = memory.recall("restroom")

    assert recalled is not None
    assert recalled.bearing_deg == pytest.approx(stored)


# ------------------------------------------------------------------ immutability


def test_publishing_never_mutates_the_sighting_it_was_given() -> None:
    memory = _memory(FakeClock())
    sighting = _sign(bearing=20.0)
    memory.publish(sighting)
    memory.publish(_sign(bearing=95.0))

    assert sighting.bearing_deg == pytest.approx(20.0)
    assert sighting.seen_count == 1


def test_a_snapshot_of_places_is_unaffected_by_later_publishes() -> None:
    memory = _memory(FakeClock())
    memory.publish(_sign())

    snapshot = memory.places()
    memory.publish(_sign(text="EXIT"))

    assert len(snapshot) == 1
    assert len(memory.places()) == 2
