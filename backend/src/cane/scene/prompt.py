"""The words sent to the VLM, and the image labels that let its answer be
turned back into a bearing.

Direction never comes back as words: the model names an image and a
horizontal position inside it, and ``service.py`` computes the bearing with
the same geometry the detector events use. The phone then speaks that
bearing in the vocabulary the wearer already knows from hazard warnings.
"""

from __future__ import annotations

from cane.transport.messages import SceneQuery

MAX_SUBJECT_WORDS = 4
MAX_DETAIL_WORDS = 10
MAX_DESCRIPTION_WORDS = 30


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


def build_prompt(query: SceneQuery) -> str:
    utterance = query.utterance.replace('"', "'")
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
