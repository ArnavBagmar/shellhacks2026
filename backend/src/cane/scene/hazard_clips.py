"""Recorded car alarms, for showing the alarm when no car will drive at the
wearer on demand (the judges' demo).

While ``startcane full`` runs, every inference response that has a vehicle in
it is drawn (the detector's own crops with its boxes) into a rolling
``PRE_ROLL_S`` buffer. When an alarm fires, that buffer plus everything up to
``POST_ROLL_S`` after the last alarming frame is written to
``backend/logs/hazard_clips/<started>/``:

- ``clip.json``: every frame's time, rows (the same rows the hazard board
  shows, with the verdict and why), and what the phone said;
- ``f<n>.jpg``: the crop the most relevant vehicle is in, large;
- ``s<n>.jpg``: all seven crops side by side, small.

The page replays them labelled RECORDED. Nothing here is on the alarm path:
rendering and writing happen on one worker thread, and any failure is logged
and ignored.
"""

from __future__ import annotations

import json
import logging
import queue
import re
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

import cv2
import numpy as np

logger = logging.getLogger(__name__)

#: backend/logs/hazard_clips (logs/ is gitignored).
DEFAULT_DIR = Path(__file__).resolve().parents[3] / "logs" / "hazard_clips"
CLIPS_DIR_ENV = "CANE_CLIPS_DIR"
PRE_ROLL_S = 6.0
POST_ROLL_S = 4.0
MAX_CLIP_S = 30.0
FOCUS_PX = 480
STRIP_PX = 160
#: Frames waiting to be drawn; older ones are dropped if the laptop is slow.
MAX_PENDING = 12
CLIP_ID = re.compile(r"^\d{8}-\d{6}(-\d+)?$")


def clips_dir() -> Path:
    import os

    configured = os.environ.get(CLIPS_DIR_ENV)
    return Path(configured) if configured else DEFAULT_DIR


def _is_vehicle(row: dict[str, Any]) -> bool:
    from cane.scene.hazard_view import VEHICLES

    return row.get("cls") in VEHICLES


def focus_row(rows: list[dict[str, Any]]) -> dict[str, Any] | None:
    """The row the replay centres on: an alarm first, then the vehicle
    closing fastest, then any vehicle.
    """
    vehicles = [r for r in rows if _is_vehicle(r)]
    if not vehicles:
        return None
    return max(
        vehicles,
        key=lambda r: (r["kind"].startswith("alarm"), r["closing_mps"] is not None, r["closing_mps"] or -99.0),
    )


@dataclass
class _Frame:
    t_ms: int
    rows: list[dict[str, Any]]
    focus_crop: int | None
    focus_jpeg: bytes
    strip_jpeg: bytes


@dataclass
class _Clip:
    started_ms: int
    frames: list[_Frame]
    alarm_ms: int
    last_alarm_ms: int
    said: list[dict[str, Any]] = field(default_factory=list)


class HazardRecorder:
    """Offered every hazard response from the event loop; draws and writes on
    its own thread.
    """

    def __init__(
        self,
        live_frame: Callable[[], np.ndarray | None] | None,
        directory: Path | None = None,
        *,
        pre_roll_s: float = PRE_ROLL_S,
        post_roll_s: float = POST_ROLL_S,
        max_clip_s: float = MAX_CLIP_S,
    ) -> None:
        self._live_frame = live_frame
        self.directory = directory or clips_dir()
        self._pre_ms = int(pre_roll_s * 1000)
        self._post_ms = int(post_roll_s * 1000)
        self._max_ms = int(max_clip_s * 1000)
        self._lock = threading.Lock()
        self._buffer: deque[_Frame] = deque()
        self._clip: _Clip | None = None
        self._pending: queue.Queue[tuple[int, list[dict[str, Any]]] | None] = queue.Queue()
        self._thread: threading.Thread | None = None
        self.saved: list[str] = []

    # ------------------------------------------------------------- writers

    def offer(self, t_ms: int, rows: list[dict[str, Any]]) -> None:
        """One response's rows. Cheap: the worker skips drawing frames
        without a vehicle unless a clip is being recorded.
        """
        if self._live_frame is None:
            return
        if self._pending.qsize() >= MAX_PENDING:
            try:
                self._pending.get_nowait()
            except queue.Empty:
                pass
        self._pending.put((t_ms, rows))
        self._ensure_thread()

    def note_said(self, t_ms: int, text: str) -> None:
        """The phone's words for the alarm in progress."""
        with self._lock:
            if self._clip is not None:
                self._clip.said.append({"t_ms": t_ms, "text": text})

    def flush(self, timeout_s: float = 5.0) -> None:
        """Tests: wait until everything offered is drawn."""
        deadline = time.monotonic() + timeout_s
        while self._pending.unfinished_tasks and time.monotonic() < deadline:
            time.sleep(0.01)

    # ------------------------------------------------------------- worker

    def _ensure_thread(self) -> None:
        if self._thread is None or not self._thread.is_alive():
            self._thread = threading.Thread(target=self._run, name="hazard-clips", daemon=True)
            self._thread.start()

    def _run(self) -> None:
        while True:
            item = self._pending.get()
            try:
                if item is not None:
                    self._step(*item)
            except Exception:  # noqa: BLE001 - recording must never cost an alarm
                logger.exception("hazard clip: frame dropped")
            finally:
                self._pending.task_done()

    def _step(self, t_ms: int, rows: list[dict[str, Any]]) -> None:
        with self._lock:
            recording = self._clip is not None
        frame = self._draw(t_ms, rows) if recording or any(_is_vehicle(r) for r in rows) else None
        alarming = any(r["kind"].startswith("alarm") for r in rows)
        finished: _Clip | None = None
        with self._lock:
            clip = self._clip
            if clip is None:
                if frame is not None:
                    self._buffer.append(frame)
                while self._buffer and self._buffer[0].t_ms < t_ms - self._pre_ms:
                    self._buffer.popleft()
                if alarming:
                    self._clip = _Clip(
                        started_ms=self._buffer[0].t_ms if self._buffer else t_ms,
                        frames=list(self._buffer), alarm_ms=t_ms, last_alarm_ms=t_ms,
                    )
                    self._buffer.clear()
                return
            if frame is not None:
                clip.frames.append(frame)
            if alarming:
                clip.last_alarm_ms = t_ms
            if t_ms - clip.last_alarm_ms > self._post_ms or t_ms - clip.started_ms > self._max_ms:
                finished, self._clip = clip, None
        if finished is not None:
            self._write(finished)

    def _draw(self, t_ms: int, rows: list[dict[str, Any]]) -> _Frame | None:
        from cane.bridge.dewarp import dewarp_equirect
        from cane.scene.hazard_view import draw_rows

        image = self._live_frame() if self._live_frame is not None else None
        if image is None:
            return None
        crops = dewarp_equirect(image, FOCUS_PX, t_arrival_laptop_ms=0)
        tiles = [tile.copy() for tile in crops.images]
        draw_rows(tiles, rows)
        focus = focus_row(rows)
        focus_crop = focus["crop_index"] if focus is not None and 0 <= focus["crop_index"] < len(tiles) else None
        # Unlabelled: the page names the crops (``focus_crop``, crop order).
        big = tiles[focus_crop if focus_crop is not None else 0]
        small = [cv2.resize(tile, (STRIP_PX, STRIP_PX), interpolation=cv2.INTER_AREA) for tile in tiles]
        ok_big, big_buf = cv2.imencode(".jpg", big, [cv2.IMWRITE_JPEG_QUALITY, 80])
        ok_strip, strip_buf = cv2.imencode(".jpg", np.hstack(small), [cv2.IMWRITE_JPEG_QUALITY, 70])
        if not (ok_big and ok_strip):
            return None
        return _Frame(t_ms, rows, focus_crop, big_buf.tobytes(), strip_buf.tobytes())

    def _write(self, clip: _Clip) -> None:
        if not clip.frames:
            return
        stamp = time.strftime("%Y%m%d-%H%M%S", time.localtime(clip.started_ms / 1000))
        target = self.directory / stamp
        n = 1
        while target.exists():
            n += 1
            target = self.directory / f"{stamp}-{n}"
        target.mkdir(parents=True)
        for i, frame in enumerate(clip.frames):
            (target / f"f{i:03d}.jpg").write_bytes(frame.focus_jpeg)
            (target / f"s{i:03d}.jpg").write_bytes(frame.strip_jpeg)
        from cane.scene.hazard_view import APPROACH_MPS, STOP_CONFIDENCE, VEHICLES, crop_names

        doc = {
            "id": target.name,
            "started_ms": clip.started_ms,
            "ended_ms": clip.frames[-1].t_ms,
            "alarm_ms": clip.alarm_ms,
            "rules": {"vehicles": sorted(VEHICLES), "approach_mps": APPROACH_MPS, "stop_confidence": STOP_CONFIDENCE},
            "crop_names": crop_names(),
            "frames": [{"t_ms": f.t_ms, "focus_crop": f.focus_crop, "rows": f.rows} for f in clip.frames],
            "said": clip.said,
        }
        (target / "clip.json").write_text(json.dumps(doc))
        self.saved.append(target.name)
        logger.info("hazard clip saved: %s (%d frames)", target, len(clip.frames))


def list_clips(directory: Path | None = None) -> list[dict[str, Any]]:
    """Newest first: what the page's clip picker shows."""
    directory = directory or clips_dir()
    out = []
    if not directory.is_dir():
        return out
    for child in directory.iterdir():
        if not CLIP_ID.match(child.name):
            continue
        try:
            doc = json.loads((child / "clip.json").read_text())
        except (OSError, ValueError):
            continue
        alarm_rows = [r for f in doc["frames"] for r in f["rows"] if r["kind"].startswith("alarm")]
        top = max(alarm_rows, key=lambda r: r["closing_mps"] or 0.0) if alarm_rows else None
        out.append({
            "id": doc["id"], "started_ms": doc["started_ms"], "frames": len(doc["frames"]),
            "duration_s": round((doc["ended_ms"] - doc["started_ms"]) / 1000, 1),
            "cls": top["cls"] if top else None,
            "max_closing_mps": top["closing_mps"] if top else None,
            "said": [s["text"] for s in doc.get("said", [])][:1],
        })
    out.sort(key=lambda c: c["started_ms"], reverse=True)
    return out


def clip_file(name: str, file: str, directory: Path | None = None) -> bytes | None:
    """One file of one clip, refusing anything that is not a clip's own."""
    if not CLIP_ID.match(name) or not re.fullmatch(r"clip\.json|[fs]\d{3}\.jpg", file):
        return None
    path = (directory or clips_dir()) / name / file
    try:
        return path.read_bytes()
    except OSError:
        return None
