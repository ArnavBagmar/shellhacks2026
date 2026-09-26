"""The words sent to the VLM, and the image labels that let its answer be
turned back into a bearing.

Direction never comes back as words: the model names an image and a
horizontal position inside it, and ``service.py`` computes the bearing with
the same geometry the detector events use. The phone then speaks that
bearing in the vocabulary the wearer already knows from hazard warnings.
"""

from __future__ import annotations

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


def crop_label(index: int, yaw_deg: float) -> str:
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
    "subject = what you see, at most {subject_words} words (e.g. \"restroom sign\"); "
    "detail = at most {detail_words} words that help reach it (e.g. \"past the elevators\"), "
    "or null; confidence = \"low\", \"medium\" or \"high\". "
    "Never use direction words like left, right or behind -- the position is given by "
    "image_index and x_center. If you are not reasonably sure, answer \"not_found\"."
)

_DESCRIBE = (
    "They asked: \"{utterance}\". Describe {scope} in at most {words} words, most useful "
    "for walking first: paths, doors, stairs, obstacles, signs, people. "
    "Answer with JSON: status \"described\"; detail = the description; everything else null. "
    "Do not guess at things you cannot see."
)


_ASK = (
    "They asked: \"{utterance}\". Answer like a sharp friend standing next to them: at most "
    "{words} words, one or two short sentences of plain speech (it is read aloud), no lists. "
    "Put what is physically here first: if the question is about their surroundings -- where "
    "something is, what is nearby, what a sign or screen says, whether it looks like rain -- "
    "answer from the images. If it is general knowledge, just answer it. If it needs live facts "
    "you cannot see or look up (a forecast, the news), say so in a few words and add anything "
    "visible that helps. "
    "Answer with JSON. If the answer is one specific thing you can see: status \"found\", "
    "image_index = the image it is in, x_center = its horizontal centre in that image from 0.0 "
    "(left edge) to 1.0 (right edge), subject = it in at most {subject_words} words, detail = "
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
    query: SceneQuery, *, now: datetime | None = None, location: str | None = None, facts: str | None = None
) -> str:
    utterance = query.utterance.replace('"', "'")
    if query.kind == "ask":
        body = _ASK.format(
            utterance=utterance,
            words=MAX_ASK_WORDS,
            subject_words=MAX_SUBJECT_WORDS,
            detail_words=MAX_DETAIL_WORDS,
        )
        return _PREAMBLE + context_line(now or datetime.now(), location, facts) + body
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
    return _PREAMBLE + body
