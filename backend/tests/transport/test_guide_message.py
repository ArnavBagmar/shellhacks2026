"""The ``guide`` frame: "guide my hand" live, phone -> laptop, display-only."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

from cane.transport.messages import GuideUpdate, MalformedMessageError, decode

SCHEMA_PATH = Path(__file__).resolve().parents[3] / "contracts" / "transport_envelope.schema.json"


def _frame(**overrides: object) -> dict[str, object]:
    frame: dict[str, object] = {
        "type": "guide", "t_phone_ms": 1_790_000_000_000, "state": "guiding", "target": "rubik's cube",
        "box": {"x_min": 0.58, "y_min": 0.52, "x_max": 0.7, "y_max": 0.66}, "hand": {"x": 0.31, "y": 0.6},
        "gap_m": 0.14, "metric": True, "hint": "Right.", "frame": None,
    }
    frame.update(overrides)
    return frame


def test_the_schema_lists_guide_and_its_example_decodes() -> None:
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    assert {"$ref": "#/$defs/guide"} in schema["oneOf"]
    validator = Draft202012Validator(schema)
    for example in schema["$defs"]["guide"]["examples"]:
        validator.validate(example)
        assert isinstance(decode(json.dumps(example)), GuideUpdate)


def test_integers_where_json_dropped_the_decimal_point_are_fine() -> None:
    """Swift's JSONSerialization writes 0.0 as 0: a box at the frame's edge."""
    msg = decode(json.dumps(_frame(box={"x_min": 0, "y_min": 0, "x_max": 1, "y_max": 1}, gap_m=0)))
    assert isinstance(msg, GuideUpdate)
    assert msg.box is not None and msg.box.x_max == 1.0


def test_no_hand_and_a_frame() -> None:
    msg = decode(json.dumps(_frame(state="find_hand", hand=None, gap_m=None, hint=None, metric=False,
                                   frame={"jpeg_b64": "/9j/4AAQ", "hfov_deg": 70, "width": 480, "height": 360})))
    assert isinstance(msg, GuideUpdate)
    assert msg.hand is None and msg.frame is not None and msg.frame.width == 480


@pytest.mark.parametrize(
    ("overrides", "schema_sees_it"),
    [
        ({"state": "dancing"}, True),
        ({"hand": {"x": 1.5, "y": 0.5}}, True),
        # JSON Schema cannot compare two fields; only the code checks order.
        ({"box": {"x_min": 0.7, "y_min": 0.5, "x_max": 0.6, "y_max": 0.6}}, False),
        ({"gap_m": -0.1}, True),
        ({"extra": 1}, True),
        ({"metric": "yes"}, True),
    ],
)
def test_nonsense_is_malformed(overrides: dict[str, object], schema_sees_it: bool) -> None:
    with pytest.raises(MalformedMessageError):
        decode(json.dumps(_frame(**overrides)))
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    assert bool(list(Draft202012Validator(schema).iter_errors(_frame(**overrides)))) == schema_sees_it


async def test_the_server_hands_guide_frames_to_the_page_and_survives_a_bad_hook() -> None:
    import asyncio

    import websockets

    from cane.transport.server import EventLinkServer

    seen: list[GuideUpdate] = []

    async with EventLinkServer("127.0.0.1", 0, ping_interval_s=0.05, on_guide=seen.append) as srv:
        async with websockets.connect(f"ws://127.0.0.1:{srv.port}") as phone:
            await phone.send(json.dumps(_frame()))
            for _ in range(100):
                if seen:
                    break
                await asyncio.sleep(0.01)
    assert seen and seen[0].target == "rubik's cube"

    def explode(update: GuideUpdate) -> None:
        raise RuntimeError("page blew up")

    async with EventLinkServer("127.0.0.1", 0, ping_interval_s=0.05, on_guide=explode) as srv:
        async with websockets.connect(f"ws://127.0.0.1:{srv.port}") as phone:
            await phone.send(json.dumps(_frame()))
            await phone.recv()
            await phone.recv()  # still connected: pings keep coming


def test_the_idle_camera_has_no_target_and_a_guide_always_has_one() -> None:
    idle = _frame(state="idle", target=None, box=None, hand=None, gap_m=None, metric=False, hint=None)
    assert isinstance(decode(json.dumps(idle)), GuideUpdate)
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    Draft202012Validator(schema).validate(idle)
    with pytest.raises(MalformedMessageError):
        decode(json.dumps({**idle, "target": "cup"}))
    with pytest.raises(MalformedMessageError):
        decode(json.dumps(_frame(target=None)))
