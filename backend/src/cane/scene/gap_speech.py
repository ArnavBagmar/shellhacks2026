"""A ring reading -> the words the wearer hears (plan step 26 B and C).

Two rules shape every string in here.

**The side is computed, never quoted.** ``prompt.py``'s principle is that
direction never comes back from the model as words; the same principle
applies outward. The sector words below are chosen from the bridge's
geometric ``gap_bearing_deg``, and they are the *phone's own* vocabulary
(``CaneOutput/BearingSector.swift``) so a wearer learns one language for
hazards, for found objects and for the way ahead. The reason the side is
rendered as words at all -- rather than as a second ``bearing_deg`` -- is that
the ``answer`` contract is frozen with one bearing field and only a ``found``
answer may carry it, so a description has no other channel.

**Nothing here may state a distance**, and that holds whether or not the
frame is anchored. When it is not, the scale-free rule that runs is a
ranking: it knows which way is *freer*, not metres, and a closing wall is
invisible to it (Sec 3.3 ruling 3). So the vocabulary is comparative --
"most open to your right" -- and a test sweeps every bearing to prove no
digit and no unit ever appears.

*Amended after step 27 (this module's original premise was "in the field
``anchored`` is false", which is no longer true):* the affine fit anchors all
three forward crops of a real X4 frame, so metres are now recoverable. The
wording still does not use them, deliberately -- a wearer navigating by ear
acts on "which way", and Sec 3.5 ruling 1 means an unanchored ring is the
one case where the ring may be *wrong* about the side, which is a reason for
less confident phrasing, never more. ``anchored`` changes nothing here: a
step that decides to speak metres has to come through this module and break
that test on purpose.
"""

from __future__ import annotations

from typing import Final

from cane.scene.gap import ClearWay

#: ``BearingSector.swift``'s boundaries, and its ownership of them: a
#: magnitude of exactly 15 or 60 belongs to the *wider* sector. The ring is
#: forward-only (+-90), so the rear sectors of that enum cannot occur here.
AHEAD_MAX_DEG: Final = 15.0
QUARTER_MAX_DEG: Final = 60.0

AHEAD: Final = "straight ahead"
QUARTER_LEFT: Final = "ahead to the left"
QUARTER_RIGHT: Final = "ahead to the right"
LATERAL_LEFT: Final = "to your left"
LATERAL_RIGHT: Final = "to your right"

#: The ``blocked`` message. Plan Sec 3.2 ruling 4: the haptic alphabet may not
#: gain a sixth *message*, so this sentence is the only place in the whole
#: system a wearer learns that the path ahead is not trustworthy.
NO_CLEAR_PATH: Final = "no clear path ahead"
#: Comparative, never metric -- see the module docstring.
MOST_OPEN: Final = "most open {phrase}"
_JOIN: Final = ", "


def sector_phrase(bearing_deg: float) -> str:
    """The phone's five-sector words for a forward bearing."""
    magnitude = abs(bearing_deg)
    if magnitude < AHEAD_MAX_DEG:
        return AHEAD
    left = bearing_deg < 0.0
    if magnitude < QUARTER_MAX_DEG:
        return QUARTER_LEFT if left else QUARTER_RIGHT
    return LATERAL_LEFT if left else LATERAL_RIGHT


def clear_way_fragment(way: ClearWay) -> str:
    """A lower-case fragment, for an answer whose words continue after it.

    When the ring is ``blocked`` *and* its least-bad column is dead ahead
    there is no side to offer, so none is claimed: "no clear path ahead, most
    open straight ahead" is a contradiction spoken aloud, and a structureless
    ring is exactly the case that produces it (plan Sec 3.3 ruling 3).
    """
    phrase = sector_phrase(way.bearing_deg)
    if not way.blocked:
        return MOST_OPEN.format(phrase=phrase)
    if phrase == AHEAD:
        return NO_CLEAR_PATH
    return NO_CLEAR_PATH + _JOIN + MOST_OPEN.format(phrase=phrase)


def clear_way_sentence(way: ClearWay) -> str:
    """The same words as a sentence, for an answer the phone speaks verbatim.

    ``AnswerPhrasing`` reads a ``described`` answer's ``detail`` straight out
    loud, so it has to be capitalised and closed; a ``found`` answer inlines
    it after a comma, so that one takes :func:`clear_way_fragment`.
    """
    fragment = clear_way_fragment(way)
    return fragment[0].upper() + fragment[1:] + "."
