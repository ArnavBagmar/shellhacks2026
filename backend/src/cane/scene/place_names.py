"""What the wearer asked for, matched against what a sign says.

Two rules earn this its own module. The first is a near-miss that substring
matching gets wrong in the most embarrassing possible way: ``room`` is inside
``restroom``, so "where is the restroom" would answer with office 100. Every
comparison here is therefore on **whole words**, never substrings.

The second is that corridors are signed in ranges. A plate reading
"ROOMS 100-110" is the only sign that will ever name room 104, so a target
number has to be tested against ranges as well as against exact plates. That
test has to see the hyphen, which is why the numbers are read off the raw
text before :func:`normalize` flattens punctuation away.

Nothing here reads a clock or does any I/O; it is pure string work so that
the store and the scanner above it stay easy to test.
"""

from __future__ import annotations

import enum
import re
from typing import Final

#: Words a wearer says around the thing they actually want. Dropping them is
#: what lets "the nearest bathroom" and "bathroom" be one target.
FILLER_WORDS: Final = frozenset(
    {
        "the", "a", "an", "any", "some", "my",
        "nearest", "closest", "next",
        "please", "to", "me", "is", "there", "find", "where",
    }
)

#: Every word a building actually hangs on a toilet door. Deliberately no
#: bare "men"/"women" -- those appear on far too much other signage to be
#: safe evidence on their own.
_RESTROOM_WORDS: Final = frozenset(
    {
        "restroom", "restrooms",
        "bathroom", "bathrooms",
        "toilet", "toilets",
        "washroom", "washrooms",
        "wc", "lavatory", "lavatories",
    }
)

_EXIT_WORDS: Final = frozenset({"exit", "exits"})
_ELEVATOR_WORDS: Final = frozenset({"elevator", "elevators", "lift", "lifts"})
_STAIR_PREFIX: Final = "stair"  # stairs, stairwell, stairway
_ROOM_WORDS: Final = frozenset({"room", "rooms", "suite", "office"})

_RANGE_RE: Final = re.compile(r"(\d+)\s*[-–—]\s*(\d+)")
_NUMBER_RE: Final = re.compile(r"\d+")
_NOT_WORD_RE: Final = re.compile(r"[^a-z0-9]+")


class PlaceKind(enum.Enum):
    """What sort of place a sign points to.

    The kind is how a target and a sign are compared: same kind is a match
    for the singular landmarks, and for ``ROOM`` the numbers must agree too.
    ``OTHER`` falls back to plain word matching, so a wearer can still ask
    for a cafe by name.
    """

    RESTROOM = "restroom"
    ROOM = "room"
    EXIT = "exit"
    ELEVATOR = "elevator"
    STAIRS = "stairs"
    OTHER = "other"


def normalize(text: str) -> str:
    """Bare lowercase words, with spoken filler removed.

    Applied to both sides: a sign's words and a wearer's sentence have to end
    up in the same alphabet before they can be compared.
    """
    flattened = _NOT_WORD_RE.sub(" ", text.lower()).strip()
    if not flattened:
        return ""
    kept = [word for word in flattened.split() if word not in FILLER_WORDS]
    return " ".join(kept)


def words(text: str) -> tuple[str, ...]:
    """The normalized words of ``text``, as a tuple for whole-word tests."""
    normalized = normalize(text)
    return tuple(normalized.split()) if normalized else ()


def classify(text: str) -> PlaceKind:
    """The kind of place ``text`` names.

    Order matters: the restroom words are tested before the room words so a
    sign reading "RESTROOM" is never read as a numbered room, and the room
    test requires a number so that a bare "ROOM" plate with no digits stays
    ``OTHER`` rather than pretending to be a specific room.
    """
    found = words(text)
    if not found:
        return PlaceKind.OTHER
    present = set(found)
    if _RESTROOM_WORDS & present:
        return PlaceKind.RESTROOM
    if _EXIT_WORDS & present:
        return PlaceKind.EXIT
    if _ELEVATOR_WORDS & present:
        return PlaceKind.ELEVATOR
    if any(word.startswith(_STAIR_PREFIX) for word in found):
        return PlaceKind.STAIRS
    if _numbers(text) and (_ROOM_WORDS & present or _is_bare_number(found)):
        return PlaceKind.ROOM
    return PlaceKind.OTHER


#: How each landmark kind is said aloud. A room says its own number, because
#: that is the useful part; the others say what they are.
_SPOKEN_KINDS: Final = {
    PlaceKind.RESTROOM: "restroom sign",
    PlaceKind.EXIT: "exit sign",
    PlaceKind.ELEVATOR: "elevator sign",
    PlaceKind.STAIRS: "stairs sign",
}


def spoken_subject(sign_text: str) -> str:
    """The sign, as a phrase a voice can say.

    Signage is printed in capitals and a synthesiser shouts capitals, so the
    plate is never read back verbatim. For the landmark kinds the phrase says
    what the sign *is* ("restroom sign") -- which is also what the existing
    hazard vocabulary sounds like. A room keeps its number, lowercased but
    otherwise as printed, because "rooms 100-110" is the information.
    """
    kind = classify(sign_text)
    named = _SPOKEN_KINDS.get(kind)
    if named is not None:
        return named
    cleaned = " ".join(sign_text.split())
    if kind is PlaceKind.ROOM:
        return cleaned.lower()
    return cleaned.title() if cleaned.isupper() else cleaned


def matches(target: str, sign_text: str) -> bool:
    """Whether ``sign_text`` answers a wearer asking for ``target``.

    Returns ``False`` for an empty or filler-only target, so a misheard word
    can never match the first sign in memory.
    """
    wanted = words(target)
    if not wanted:
        return False

    target_kind = classify(target)
    sign_kind = classify(sign_text)

    if target_kind is PlaceKind.ROOM:
        return sign_kind is PlaceKind.ROOM and _room_number_matches(target, sign_text)
    if target_kind is not PlaceKind.OTHER:
        return target_kind is sign_kind
    return _every_word_appears(wanted, words(sign_text))


# ------------------------------------------------------------------ numbers


def _numbers(text: str) -> tuple[int, ...]:
    """Every standalone number in ``text``, read off the raw string."""
    return tuple(int(found) for found in _NUMBER_RE.findall(text))


def _ranges(text: str) -> tuple[tuple[int, int], ...]:
    """Every ``100-110`` style span, read before punctuation is flattened."""
    return tuple((int(low), int(high)) for low, high in _RANGE_RE.findall(text))


def _is_bare_number(found: tuple[str, ...]) -> bool:
    """A plate that is only digits -- the number beside a door."""
    return all(word.isdigit() for word in found)


def _room_number_matches(target: str, sign_text: str) -> bool:
    """A target room number against a plate or a range plate.

    A range is inclusive at both ends: the sign claims those doors.
    """
    wanted = _numbers(target)
    if not wanted:
        return False
    number = wanted[0]
    spans = _ranges(sign_text)
    if spans:
        return any(low <= number <= high for low, high in spans)
    return number in _numbers(sign_text)


def _every_word_appears(wanted: tuple[str, ...], available: tuple[str, ...]) -> bool:
    """Whole-word containment, used only when the target names no known kind."""
    if not available:
        return False
    present = set(available)
    return all(word in present for word in wanted)
