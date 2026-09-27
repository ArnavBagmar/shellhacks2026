"""Matching a spoken target against the words on a sign.

Every case here is a sentence the wearer might actually say against a sign a
building might actually hang. The two that matter most are the near-misses:
"restroom" must not match "ROOM 100" just because ``room`` is a substring of
``restroom``, and a room-range plate ("ROOMS 100-110") must answer for the
room inside it, because that is how corridors are actually signed.
"""

from __future__ import annotations

import pytest

from cane.scene.place_names import PlaceKind, classify, matches, normalize, spoken_subject

# ------------------------------------------------------------------ normalize


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("RESTROOMS", "restrooms"),
        ("  Room   100  ", "room 100"),
        ("Room-100", "room 100"),  # a hyphen inside a label is a separator
        ("EXIT →", "exit"),  # arrows and punctuation are not words
        ("the nearest bathroom", "bathroom"),  # spoken filler is stripped
        ("a restroom please", "restroom"),
        ("", ""),
    ],
)
def test_normalize_reduces_both_signs_and_speech_to_bare_words(raw: str, expected: str) -> None:
    assert normalize(raw) == expected


# ------------------------------------------------------------------ classify


@pytest.mark.parametrize(
    "text",
    ["RESTROOM", "RESTROOMS", "Bathroom", "TOILET", "toilets", "WC", "WASHROOM", "LAVATORY"],
)
def test_every_restroom_word_a_building_uses_classifies_as_restroom(text: str) -> None:
    assert classify(text) is PlaceKind.RESTROOM


@pytest.mark.parametrize(
    ("text", "kind"),
    [
        ("ROOM 100", PlaceKind.ROOM),
        ("ROOMS 100-110", PlaceKind.ROOM),
        ("100", PlaceKind.ROOM),  # a bare number plate beside a door
        ("EXIT", PlaceKind.EXIT),
        ("ELEVATORS", PlaceKind.ELEVATOR),
        ("STAIRS", PlaceKind.STAIRS),
        ("STAIRWELL B", PlaceKind.STAIRS),
        ("Starbucks", PlaceKind.OTHER),
        ("", PlaceKind.OTHER),
    ],
)
def test_classify_names_the_kind_of_place_a_sign_points_to(text: str, kind: PlaceKind) -> None:
    assert classify(text) is kind


# ------------------------------------------------------------------ matches


@pytest.mark.parametrize(
    "target",
    ["restroom", "bathroom", "the nearest bathroom", "a toilet", "washroom", "restrooms"],
)
def test_any_way_of_asking_for_a_restroom_matches_a_restroom_sign(target: str) -> None:
    assert matches(target, "RESTROOMS") is True


def test_asking_for_a_restroom_does_not_match_a_numbered_room() -> None:
    """``room`` is a substring of ``restroom``; substring matching would send
    the wearer to office 100 when they need a toilet."""
    assert matches("restroom", "ROOM 100") is False
    assert matches("bathroom", "ROOM 100") is False


def test_a_room_number_matches_its_own_plate() -> None:
    assert matches("room 100", "ROOM 100") is True
    assert matches("100", "Room 100") is True
    assert matches("room one hundred", "ROOM 100") is False  # no spelled-out numbers yet


def test_a_room_number_matches_the_range_plate_that_contains_it() -> None:
    assert matches("room 104", "ROOMS 100-110") is True
    assert matches("room 100", "ROOMS 100-110") is True  # inclusive at both ends
    assert matches("room 110", "ROOMS 100-110") is True


def test_a_room_number_outside_the_range_does_not_match() -> None:
    assert matches("room 120", "ROOMS 100-110") is False
    assert matches("room 99", "ROOMS 100-110") is False


def test_the_wrong_room_number_does_not_match() -> None:
    assert matches("room 100", "ROOM 101") is False


@pytest.mark.parametrize(
    ("target", "sign"),
    [
        ("exit", "EXIT"),
        ("the exit", "EMERGENCY EXIT"),
        ("elevator", "ELEVATORS"),
        ("stairs", "STAIRWELL B"),
    ],
)
def test_the_other_landmark_kinds_match_their_signs(target: str, sign: str) -> None:
    assert matches(target, sign) is True


def test_an_unrelated_target_matches_nothing() -> None:
    assert matches("starbucks", "RESTROOMS") is False
    assert matches("restroom", "EXIT") is False


@pytest.mark.parametrize("target", ["", "   ", "the", "a"])
def test_an_empty_or_filler_only_target_never_matches(target: str) -> None:
    """A misheard target must not match the first sign in memory."""
    assert matches(target, "RESTROOMS") is False


def test_an_unclassified_sign_still_matches_on_its_own_words() -> None:
    """A cafe is ``OTHER``, but the wearer asking for it by name should find it."""
    assert matches("starbucks", "Starbucks") is True
    assert matches("the cafe", "CAFE") is True


# ------------------------------------------------------------------ speaking


@pytest.mark.parametrize(
    ("sign_text", "spoken"),
    [
        ("RESTROOMS", "restroom sign"),
        ("Toilet", "restroom sign"),
        ("EXIT", "exit sign"),
        ("EMERGENCY EXIT", "exit sign"),
        ("ELEVATORS", "elevator sign"),
        ("STAIRWELL B", "stairs sign"),
        ("ROOM 100", "room 100"),  # the number is the useful part, said as printed
        ("ROOMS 100-110", "rooms 100-110"),
        ("STARBUCKS", "Starbucks"),  # shouted caps would be shouted by the TTS
        ("CAFE", "Cafe"),
    ],
)
def test_a_sign_becomes_something_a_voice_can_say(sign_text: str, spoken: str) -> None:
    """The wearer hears a phrase, not a plate. "RESTROOMS" read verbatim is
    shouted by the synthesiser and says less than "restroom sign"."""
    assert spoken_subject(sign_text) == spoken
