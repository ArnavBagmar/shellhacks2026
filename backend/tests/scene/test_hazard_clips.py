"""Recorded car alarms: the seconds before an alarm and after it are saved
with the detector's view, the verdicts and what the phone said, and the page
can list and fetch them (and nothing else).
"""

from __future__ import annotations

import json
import urllib.request
from pathlib import Path
from types import SimpleNamespace

import numpy as np

from cane.scene.debug_view import DebugHub, serve_debug
from cane.scene.hazard_clips import HazardRecorder, clip_file, focus_row, list_clips

EQUIRECT = np.full((180, 360, 3), 90, np.uint8)


def _row(kind: str, closing: float | None, *, cls: str = "car", track: str = "c1") -> dict:
    return {
        "track_id": track, "cls": cls, "confidence": 0.9, "bearing_deg": -60.0, "range_band": "near",
        "closing_mps": closing, "kind": kind, "why": "", "sent": True, "built": True, "crop_index": 5,
        "box": [0.5, 0.5, 0.2, 0.2],
    }


def _recorder(tmp_path: Path) -> HazardRecorder:
    return HazardRecorder(lambda: EQUIRECT, tmp_path, pre_roll_s=2.0, post_roll_s=1.0)


def test_an_alarm_is_saved_with_the_seconds_before_and_after(tmp_path: Path) -> None:
    rec = _recorder(tmp_path)
    t = 1_000_000
    rec.offer(t - 5000, [_row("not_coming", 0.2)])  # too early: outside the pre-roll
    for dt, closing in ((-1500, 0.8), (-1000, 1.2), (-500, 1.4)):
        rec.offer(t + dt, [_row("not_coming", closing)])
    rec.offer(t, [_row("alarm", 2.5)])
    rec.flush()
    rec.note_said(t + 200, "Stop! Car coming from your left.")
    rec.offer(t + 500, [_row("alarm", 3.0)])
    rec.offer(t + 1000, [_row("ignored", None, cls="person")])  # kept: a clip is running
    rec.offer(t + 2600, [])  # 2.1 s after the last alarm: the clip ends
    rec.flush()

    (clip,) = list_clips(tmp_path)
    assert clip["cls"] == "car" and clip["max_closing_mps"] == 3.0
    assert clip["said"] == ["Stop! Car coming from your left."]
    doc = json.loads((tmp_path / clip["id"] / "clip.json").read_text())
    times = [f["t_ms"] - t for f in doc["frames"]]
    assert times == [-1500, -1000, -500, 0, 500, 1000, 2600]
    assert doc["alarm_ms"] == t and doc["frames"][3]["focus_crop"] == 5
    assert doc["rules"]["approach_mps"] == 1.5
    assert (tmp_path / clip["id"] / "f000.jpg").read_bytes()[:2] == b"\xff\xd8"
    assert (tmp_path / clip["id"] / "s006.jpg").exists()


def test_nothing_is_kept_without_an_alarm_or_without_a_vehicle(tmp_path: Path) -> None:
    rec = _recorder(tmp_path)
    for i in range(10):
        rec.offer(1_000_000 + i * 200, [_row("ignored", None, cls="person"), _row("not_coming", 0.3)])
    rec.flush()
    assert list_clips(tmp_path) == []
    assert rec._buffer and all(f.t_ms >= 1_000_000 + 1800 - 2000 for f in rec._buffer)  # noqa: SLF001


def test_the_replay_centres_on_the_alarm_then_the_fastest_vehicle() -> None:
    rows = [_row("ignored", None, cls="person", track="p"), _row("not_coming", 1.0, track="slow"),
            _row("not_coming", 1.3, track="faster"), _row("waiting", None, track="new")]
    assert focus_row(rows)["track_id"] == "faster"
    assert focus_row(rows + [_row("alarm", 2.0, track="coming")])["track_id"] == "coming"
    assert focus_row([_row("ignored", None, cls="person")]) is None


def test_only_a_clips_own_files_are_served(tmp_path: Path) -> None:
    (tmp_path / "20260927-140000").mkdir()
    (tmp_path / "20260927-140000" / "clip.json").write_text("{}")
    (tmp_path / "secret.txt").write_text("no")
    assert clip_file("20260927-140000", "clip.json", tmp_path) == b"{}"
    assert clip_file("..", "secret.txt", tmp_path) is None
    assert clip_file("20260927-140000", "../../secret.txt", tmp_path) is None
    assert clip_file("20260927-140000", "f001.jpg", tmp_path) is None  # missing


def test_the_page_lists_and_serves_recorded_alarms(tmp_path: Path) -> None:
    hub = DebugHub(live_frame=lambda: EQUIRECT, source_name="x4", clips_dir=tmp_path)
    hub.clips = _recorder(tmp_path)
    t = 2_000_000
    hub.clips.offer(t, [_row("alarm", 2.5)])
    hub.clips.offer(t + 1500, [])
    hub.clips.flush()
    server = serve_debug(hub, port=0)
    try:
        base = f"http://127.0.0.1:{server.server_address[1]}"
        (clip,) = json.loads(urllib.request.urlopen(f"{base}/clips.json").read())
        doc = json.loads(urllib.request.urlopen(f"{base}/clips/{clip['id']}/clip.json").read())
        assert len(doc["frames"]) == 2
        assert urllib.request.urlopen(f"{base}/clips/{clip['id']}/f000.jpg").read()[:2] == b"\xff\xd8"
        assert b"Car alarm" in urllib.request.urlopen(f"{base}/").read()
    finally:
        server.shutdown()


def test_the_hub_records_alarms_and_the_phones_words() -> None:
    hub = DebugHub(live_frame=lambda: EQUIRECT, source_name="x4")
    seen: list[tuple] = []
    hub.clips = SimpleNamespace(offer=lambda t, rows: seen.append(("offer", rows)),
                                note_said=lambda t, text: seen.append(("said", text)), directory=Path("."))
    det = SimpleNamespace(
        cls=SimpleNamespace(value="car"), closing_mps=4.0, confidence=0.9, track_id="c2",
        range_band=SimpleNamespace(value="near"), crop_index=5, cx=0.5, cy=0.5, w=0.2, h=0.2,
    )
    hub.record_hazards(SimpleNamespace(detections=[det], t_capture_ms=0), (), ())
    hub.on_phone_diag({"kind": "hazard", "data": {"urgency": 3}})
    hub.on_phone_diag({"kind": "say", "data": {"text": "Stop! Car coming from your left."}})
    assert seen[0][0] == "offer" and seen[0][1][0]["kind"] == "alarm"
    assert seen[1] == ("said", "Stop! Car coming from your left.")


def test_a_kept_run_is_listed_first_and_served(tmp_path) -> None:
    fresh, kept = tmp_path / "fresh", tmp_path / "kept"
    for base, name, started in ((fresh, "20260927-140000", 2), (kept, "20260927-062947", 1)):
        (base / name).mkdir(parents=True)
        (base / name / "clip.json").write_text(json.dumps(
            {"id": name, "started_ms": started, "ended_ms": started + 1000, "frames": [], "said": []}))
        (base / name / "f000.jpg").write_bytes(b"\xff\xd8" + name.encode())
    listed = list_clips(fresh, kept)
    assert [(c["id"], c["kept"]) for c in listed] == [("20260927-062947", True), ("20260927-140000", False)]
    assert clip_file("20260927-062947", "f000.jpg", fresh, kept) == b"\xff\xd8" + b"20260927-062947"
    assert clip_file("20260927-140000", "f000.jpg", fresh, kept) == b"\xff\xd8" + b"20260927-140000"


def test_the_presentation_run_is_in_the_repo() -> None:
    from cane.scene.hazard_clips import KEPT_DIR

    doc = json.loads((KEPT_DIR / "20260927-062947" / "clip.json").read_text())
    assert len(doc["frames"]) == 73
    assert any(s["text"].startswith("Stop! Car coming") for s in doc["said"])
