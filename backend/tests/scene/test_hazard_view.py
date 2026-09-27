"""The hazard board on the debug page: the verdict mirrors the phone's rule,
every detection is explained, alarms are logged with what the phone said,
and vehicle tracks keep their approach-speed history.
"""

from __future__ import annotations

import json
import urllib.request
from types import SimpleNamespace

import cv2
import numpy as np
import pytest

from cane.scene.debug_view import DebugHub, serve_debug
from cane.scene.hazard_view import APPROACH_MPS, HazardBoard, verdict


def _det(cls: str, closing: float | None, *, conf: float = 0.9, track: str = "t1", crop: int = 5,
         cx: float = 0.5) -> SimpleNamespace:
    return SimpleNamespace(
        cls=SimpleNamespace(value=cls), closing_mps=closing, confidence=conf, track_id=track,
        range_band=SimpleNamespace(value="near"), crop_index=crop, cx=cx, cy=0.5, w=0.2, h=0.2,
    )


def _event(det: SimpleNamespace) -> SimpleNamespace:
    return SimpleNamespace(track_id=det.track_id, cls=det.cls)


def _response(*dets: SimpleNamespace) -> SimpleNamespace:
    return SimpleNamespace(detections=list(dets), t_capture_ms=0)


@pytest.mark.parametrize(
    ("cls", "closing", "conf", "band", "kind"),
    [
        ("person", 4.0, 0.9, "near", "ignored"),
        ("car", None, 0.9, "near", "waiting"),
        ("car", 0.0, 0.9, "near", "not_coming"),
        ("car", -3.0, 0.9, "near", "not_coming"),
        ("car", 1.2, 0.9, "near", "not_coming"),  # the wearer walking up to a parked car
        ("car", 1.4, 0.9, "near", "not_coming"),
        ("car", 2.0, 0.9, "near", "alarm"),
        ("car", APPROACH_MPS, 0.9, "near", "alarm"),
        ("bus", 5.0, 0.9, "near", "alarm"),
        ("car", 5.0, 0.4, "near", "alarm_low"),
        ("car", 1.6, 0.9, "mid", "alarm"),
        ("car", 1.4, 0.9, "mid", "not_coming"),
        ("car", 20.0, 0.9, "far", "too_far"),
        ("car", 6.0, 0.9, None, "too_far"),  # no band reads as far
    ],
)
def test_the_verdict_mirrors_the_phone(
    cls: str, closing: float | None, conf: float, band: str | None, kind: str
) -> None:
    assert verdict(cls, closing, conf, band)[0] == kind


def test_every_detection_is_explained_and_sending_is_shown() -> None:
    board = HazardBoard()
    person, parked, coming = _det("person", 2.0, track="p"), _det("car", 0.0, track="c1"), _det("car", 4.0, track="c2")
    built = tuple(_event(d) for d in (person, parked, coming))
    sent = (_event(parked), _event(coming))  # --hazards vehicles dropped the person
    board.record(_response(person, parked, coming), built, sent)

    rows = {r["track_id"]: r for r in board.state()["latest"]["rows"]}
    assert rows["p"]["kind"] == "ignored" and not rows["p"]["sent"]
    assert rows["c1"]["kind"] == "not_coming" and rows["c1"]["sent"]
    assert rows["c2"]["kind"] == "alarm" and "Stop" in rows["c2"]["why"]
    assert rows["c2"]["bearing_deg"] == pytest.approx(-60.0)  # crop 5 is 60 degrees left
    state = board.state()
    assert [a["track_id"] for a in state["alarms"]] == ["c2"]
    assert state["last_minute"]["alarms"] == 1 and state["last_minute"]["sent"] == 2


def test_an_alarm_shows_what_the_phone_then_said() -> None:
    board = HazardBoard()
    coming = _det("car", 4.0, track="c2")
    board.record(_response(coming), (_event(coming),), (_event(coming),))
    board.on_phone("hazard", {"urgency": 3})
    board.on_phone("say", {"text": "Stop! Car coming from your left."})
    assert board.state()["alarms"][0]["phone_said"] == ["Stop! Car coming from your left."]


def test_one_alarm_row_per_approach_updated_as_it_keeps_coming() -> None:
    board = HazardBoard()
    for closing in (3.2, 3.5, 5.0, 4.0):
        coming = _det("car", closing, track="c2")
        board.record(_response(coming), (_event(coming),), (_event(coming),))
    (alarm,) = board.state()["alarms"]
    assert alarm["frames"] == 4 and alarm["max_closing_mps"] == 5.0 and alarm["closing_mps"] == 4.0
    assert board.state()["last_minute"]["alarms"] == 1


def test_vehicle_tracks_keep_their_speed_history_people_do_not() -> None:
    board = HazardBoard()
    for closing in (None, 0.8, 1.9, 3.1):
        car = _det("car", closing, track="c9")
        board.record(_response(car, _det("person", 1.0, track="p")), (), ())
    tracks = board.state()["tracks"]
    assert list(tracks) == ["c9"]
    assert [p[1] for p in tracks["c9"]["points"]] == [None, 0.8, 1.9, 3.1]


def test_the_detector_view_draws_on_the_detectors_own_crops() -> None:
    board = HazardBoard()
    coming = _det("car", 4.0, track="c2", crop=0)
    board.record(_response(coming), (), ())
    equirect = np.full((180, 360, 3), 90, np.uint8)
    strip = cv2.imdecode(np.frombuffer(board.view_jpeg(lambda: equirect, 0.0) or b"", np.uint8), cv2.IMREAD_COLOR)
    assert strip.shape[1] == 7 * strip.shape[0]  # six ring crops + the floor crop
    red = (strip[:, :, 2] > 180) & (strip[:, :, 1] < 110)
    assert red[:, : strip.shape[0]].any(), "the alarm box is drawn in red on C0"


def test_the_page_serves_the_hazard_state_and_view() -> None:
    hub = DebugHub(live_frame=lambda: np.full((180, 360, 3), 90, np.uint8), source_name="x4")
    coming = _det("car", 4.0, track="c2")
    hub.record_hazards(_response(coming), (_event(coming),), (_event(coming),))
    hub.on_phone_diag({"kind": "hazard", "data": {"urgency": 3}})
    server = serve_debug(hub, port=0)
    try:
        base = f"http://127.0.0.1:{server.server_address[1]}"
        state = json.loads(urllib.request.urlopen(f"{base}/state.json").read())
        assert state["hazards"]["active"]
        assert state["hazards"]["alarms"][0]["cls"] == "car"
        assert urllib.request.urlopen(f"{base}/hazards.jpg").read()[:2] == b"\xff\xd8"
        assert b"Hazards: cars coming at you" in urllib.request.urlopen(f"{base}/").read()
    finally:
        server.shutdown()
