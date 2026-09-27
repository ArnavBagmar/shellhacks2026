"""The words sent to the VLM, and the image labels that let its answer be
turned back into a bearing.

Direction never comes back as words: the model names an image and a
horizontal position inside it, and ``service.py`` computes the bearing with
the same geometry the detector events use. The phone then speaks that
bearing in the vocabulary the wearer already knows from hazard warnings.

Step 26 sends one fact *in* the same way. The depth ring's clearest way
through is a bearing, and a bearing is exactly what ``View.bearing_at``
turns an (image, x) pair into -- so it is run backwards and the model is
told which image and which horizontal position to trust, never which side.
That keeps the principle above symmetric: no direction word crosses the
boundary in either direction, and the wearer's side word is still computed
from geometry rather than echoed back from a model.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from cane.transport.messages import SceneQuery

MAX_SUBJECT_WORDS = 4
MAX_DETAIL_WORDS = 10
MAX_DESCRIPTION_WORDS = 30
#: An ``ask`` answer is read aloud to someone standing still: two short
#: sentences at most.
MAX_ASK_WORDS = 30


def side_words(yaw_deg: float) -> str:
    """Where a view points, for the label only (0 ahead, negative left)."""
    if yaw_deg == 0:
        return "straight ahead"
    if abs(yaw_deg) == 180:
        return "directly behind"
    side = "right" if yaw_deg > 0 else "left"
    return f"{abs(yaw_deg):g} degrees to the {side}"


def crop_label(index: int, yaw_deg: float, *, tall: bool = False) -> str:
    if tall:
        return (
            f"Image {index}: 360 camera on the wearer's head, a 60-degree-wide view "
            f"centred {side_words(yaw_deg)}, tall: from above eye level down to the floor."
        )
    return (
        f"Image {index}: 360 camera on the wearer's shoulder, a 60-degree view "
        f"centred {side_words(yaw_deg)}."
    )


def phone_label(index: int, hfov_deg: float) -> str:
    return (
        f"Image {index}: phone camera on the wearer's chest, looking straight ahead, "
        f"{hfov_deg:.0f}-degree view. Sharpest image; prefer it for anything ahead."
    )


_PREAMBLE = (
    "You are the eyes of a blind pedestrian who is standing still. The images above "
    "show their surroundings; image-left is the wearer's left. "
)

_FIND = (
    "They asked: \"{utterance}\". Find a {target} or the best evidence of where one is: "
    "the thing itself, a sign or pictogram for it, or an arrow pointing to it. "
    "Answer with JSON: status \"found\" or \"not_found\"; image_index = the image it is in; "
    "x_center = its horizontal centre in that image from 0.0 (left edge) to 1.0 (right edge); "
    "box_2d = [ymin, xmin, ymax, xmax] tightly around it in that image, each 0 to 1000; "
    "subject = what you see, at most {subject_words} words (e.g. \"restroom sign\"); "
    "detail = at most {detail_words} words that help reach it (e.g. \"past the elevators\"), "
    "or null; confidence = \"low\", \"medium\" or \"high\". "
    "The phone image is live and can measure distance: if the thing is visible in it at all, "
    "image_index MUST be the phone image, even if another view shows it too. "
    "Never use direction words like left, right or behind -- the position is given by "
    "image_index and x_center. If you are not reasonably sure, answer \"not_found\"."
)

_DESCRIBE = (
    "They asked: \"{utterance}\". Describe {scope} in at most {words} words, most useful "
    "for walking first: paths, doors, stairs, obstacles, signs, people. "
    "Answer with JSON: status \"described\"; detail = the description; everything else null. "
    "Do not guess at things you cannot see."
)

#: Step 26's whole prompt growth, in four short pieces, appended *after* the
#: step-20 prompt so that prompt is byte-identical whenever there is no ring.
#: Trailing, because "trust this over your own guess" is an instruction and
#: instructions are followed best last.
_GAP_PREFIX = "A depth sensor saw the same scene: "
_GAP_AT_IMAGE = (
    "the clearest way through is in image {index} at horizontal position {x:.2f} "
    "(0 left edge, 1 right edge)"
)
#: The forward ring spans +-90 while a describe_front with a phone photo sends
#: only its ~70 degree view, so the gap can legitimately be off every image.
#: Saying so costs nothing and beats pointing at a pixel that is not there.
_GAP_OFF_IMAGE = "the clearest way through is outside these images"
_GAP_BLOCKED = ", and there is no clear path ahead"
_GAP_SUFFIX = (
    ". Trust that over your own guess about which way is clear, and state no "
    "distances. "
)
#: Asserted by a test rather than trusted. The point of the bound is the
#: latency budget: the ring reaches the model as one bearing expressed as an
#: image and an x, not as 48 floats, so the prompt grows by a sentence while
#: the request stays dominated by its six JPEGs.
MAX_GAP_CLAUSE_CHARS = 256
#: The image index, the x, and the two edge-convention digits. Nothing else.
MAX_GAP_CLAUSE_NUMBERS = 4


@dataclass(frozen=True, slots=True)
class GapHint:
    """The depth ring's clearest way, in the prompt's own vocabulary.

    ``image_index`` and ``x_center`` are both set or both ``None``: a
    position without an image to read it in is meaningless, and letting them
    drift apart is how a prompt ends up saying "image None".
    """

    image_index: int | None
    x_center: float | None
    blocked: bool

    def __post_init__(self) -> None:
        if (self.image_index is None) != (self.x_center is None):
            raise ValueError(
                "a gap hint needs an image index and an x position together, "
                f"got {self.image_index!r} and {self.x_center!r}"
            )


def gap_clause(hint: GapHint) -> str:
    """One sentence of depth evidence, or where to look for it."""
    if hint.image_index is None or hint.x_center is None:
        where = _GAP_OFF_IMAGE
    else:
        where = _GAP_AT_IMAGE.format(index=hint.image_index, x=hint.x_center)
    blocked = _GAP_BLOCKED if hint.blocked else ""
    return _GAP_PREFIX + where + blocked + _GAP_SUFFIX


_ASK = (
    "They asked: \"{utterance}\". Answer like a sharp friend standing next to them: at most "
    "{words} words, one or two short sentences of plain speech (it is read aloud), no lists. "
    "Put what is physically here first: if the question is about their surroundings -- where "
    "something is, what is nearby, what a sign or screen says, whether it looks like rain -- "
    "answer from the images. If it is general knowledge, just answer it. If it needs live facts "
    "you cannot see or look up (a forecast, the news), say so in a few words and add anything "
    "visible that helps. "
    "Answer with JSON. If the answer is one specific thing you can see: status \"found\", "
    "image_index = the image it is in (the phone image when it shows it), x_center = its horizontal "
    "centre in that image from 0.0 "
    "(left edge) to 1.0 (right edge), box_2d = [ymin, xmin, ymax, xmax] tightly around it in that "
    "image (each 0 to 1000), subject = it in at most {subject_words} words, detail = "
    "at most {detail_words} more words or null, confidence = \"low\", \"medium\" or \"high\" "
    "-- and no direction words, the position is computed from image_index and x_center. "
    "Otherwise: status \"answered\", detail = the whole answer, everything else null."
)


def context_line(now: datetime, location: str | None, facts: str | None = None) -> str:
    """Where and when (plus any looked-up facts, e.g. live weather), for
    questions the images alone cannot answer.
    """
    clock = now.strftime("%I:%M %p").lstrip("0")
    where = f" They are at {location}." if location else ""
    known = f" {facts} Use this for weather questions; mention the cameras only if they add something." if facts else ""
    return f"It is {now:%A} {now.day} {now:%B %Y}, {clock} local time.{where}{known} "


def build_prompt(
    query: SceneQuery,
    gap: GapHint | None = None,
    *,
    now: datetime | None = None,
    location: str | None = None,
    facts: str | None = None,
) -> str:
    utterance = query.utterance.replace('"', "'")
    if query.kind == "ask":
        body = _ASK.format(
            utterance=utterance,
            words=MAX_ASK_WORDS,
            subject_words=MAX_SUBJECT_WORDS,
            detail_words=MAX_DETAIL_WORDS,
        )
        # The ring belongs on an ask too: route() sends anything it cannot parse
        # here, so "is it safe to walk forward" lands on this branch and is
        # exactly the kind of question the depth evidence answers.
        return (
            _PREAMBLE
            + context_line(now or datetime.now(), location, facts)
            + body
            + ("" if gap is None else gap_clause(gap))
        )
    if query.kind == "find":
        body = _FIND.format(
            utterance=utterance,
            target=query.target,
            subject_words=MAX_SUBJECT_WORDS,
            detail_words=MAX_DETAIL_WORDS,
        )
    else:
        scope = "what is all around them" if query.kind == "describe_around" else "what is in front of them"
        body = _DESCRIBE.format(utterance=utterance, scope=scope, words=MAX_DESCRIPTION_WORDS)
    tail = "" if gap is None else gap_clause(gap)
    return _PREAMBLE + body + tail
