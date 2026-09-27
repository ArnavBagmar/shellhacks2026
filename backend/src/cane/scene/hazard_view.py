"""The hazard half of the debug page: what the detector saw, what each
detection's verdict is, what was sent to the phone, and what the phone said.

The verdict mirrors the phone's ``HazardScope.approachingVehicles`` (the
app's setting) so a tester can see *why* a car did or did not alarm:

- not a car or bus                      -> never announced;
- a vehicle with no closing speed yet   -> not yet (the laptop needs two
                                           agreeing samples);
- a vehicle closing under 1.5 m/s       -> not coming (stopped, leaving,
                                           slow, or the wearer walking up);
- coming, but ``far`` (beyond ~15 m)    -> too far, never alarms;
- close and coming                      -> ALARM, demoted one level when the
                                           box confidence is under 0.6.

Keep ``APPROACH_MPS`` / ``VEHICLES`` / ``STOP_CONFIDENCE`` equal to the Swift
side (a Swift test reads this file).
"""

from __future__ import annotations

import threading
import time
from collections import Counter, deque
from typing import Any, Callable

import cv2
import numpy as np

#: = HazardScope.approachMps (ios/CaneKit/Sources/CaneFeedback/HazardScope.swift)
APPROACH_MPS = 1.5
#: = HazardScope.isClose: near (<= 5 m) and mid (5-15 m) alarm, far never.
CLOSE_BANDS = frozenset({"near", "mid"})
#: = HazardScope.isVehicle
VEHICLES = frozenset({"car", "bus"})
#: = UrgencyPolicy.minStopConfidence
STOP_CONFIDENCE = 0.60
#: How long a vehicle's closing-speed history is kept for the chart.
TRACK_HISTORY_S = 30.0
MAX_ALARMS = 40
#: One alarm row per *approach*: a track that keeps coming updates its row;
#: a gap this long without alarming starts a new one.
ALARM_EPISODE_GAP_S = 4.0
VIEW_CROP_PX = 200

RED = (60, 60, 240)
AMBER = (0, 190, 255)
GREY = (170, 170, 170)
GREEN = (80, 210, 80)


def now_ms() -> int:
    return int(time.time() * 1000)


def verdict(cls: str, closing: float | None, confidence: float, band: str | None = "near") -> tuple[str, str]:
    """(kind, why): kind is alarm | alarm_low | waiting | not_coming | too_far | ignored.
    ``band`` None is read as far, as on the phone.
    """
    if cls not in VEHICLES:
        return "ignored", f"{cls}: not a vehicle, never announced"
    if closing is None:
        return "waiting", "vehicle, no approach speed yet (needs 2 agreeing frames)"
    if closing < APPROACH_MPS:
        what = "moving away" if closing < -0.5 else ("stopped" if abs(closing) < 0.5 else "slow")
        return "not_coming", f"vehicle {what}: closing {closing:+.1f} m/s < {APPROACH_MPS:g} m/s"
    band = band or "far"
    if band not in CLOSE_BANDS:
        return "too_far", f"coming {closing:.1f} m/s but {band} (beyond ~15 m never alarms)"
    if confidence < STOP_CONFIDENCE:
        return "alarm_low", f"COMING {closing:.1f} m/s, but box confidence {confidence:.2f} < {STOP_CONFIDENCE}: one level lower"
    return "alarm", f"COMING {closing:.1f} m/s: 'Stop! Car coming...'"


_COLOURS = {"alarm": RED, "alarm_low": RED, "waiting": AMBER, "not_coming": AMBER, "too_far": AMBER, "ignored": GREY}


class HazardBoard:
    """Thread-safe: written by the bridge's response handler and the phone's
    diagnostics, read by the page's HTTP threads.
    """

    def __init__(self, *, delta_deg: float = 0.0) -> None:
        self._lock = threading.Lock()
        self._delta_deg = delta_deg
        self._latest: dict[str, Any] | None = None
        self._latest_boxes: list[dict[str, Any]] = []
        self._responses: deque[int] = deque(maxlen=200)
        self._tracks: dict[str, deque[tuple[int, float | None]]] = {}
        self._track_cls: dict[str, str] = {}
        self._alarms: deque[dict[str, Any]] = deque(maxlen=MAX_ALARMS)
        self._open_alarm: dict[str, dict[str, Any]] = {}
        self._seen: deque[tuple[int, str, str, bool]] = deque(maxlen=4000)  # (t, cls, kind, sent)
        self._phone: deque[dict[str, Any]] = deque(maxlen=30)
        self._view_cache: tuple[float, bytes | None] = (0.0, None)
        self._render_lock = threading.Lock()

    # ------------------------------------------------------------- writers

    def record(self, response: Any, built: tuple[Any, ...], sent: tuple[Any, ...]) -> None:
        from cane.bridge.dewarp import CROPSET_YAWS_DEG
        from cane.bridge.events import bearing_for_detection

        t = now_ms()
        sent_keys = {(e.track_id, e.cls.value) for e in sent}
        built_keys = {(e.track_id, e.cls.value) for e in built}
        rows: list[dict[str, Any]] = []
        for d in response.detections:
            cls = d.cls.value
            closing = d.closing_mps
            band = d.range_band.value if d.range_band else None
            kind, why = verdict(cls, closing, d.confidence, band)
            key = (d.track_id, cls)
            try:
                bearing = round(bearing_for_detection(d, CROPSET_YAWS_DEG, self._delta_deg), 1)
            except (IndexError, ValueError):
                bearing = None
            rows.append({
                "track_id": d.track_id, "cls": cls, "confidence": round(d.confidence, 2),
                "bearing_deg": bearing, "range_band": d.range_band.value if d.range_band else None,
                "closing_mps": closing, "kind": kind, "why": why,
                "sent": key in sent_keys, "built": key in built_keys,
                "crop_index": d.crop_index, "box": [d.cx, d.cy, d.w, d.h],
            })
        with self._lock:
            self._responses.append(t)
            self._latest = {"t_ms": t, "t_capture_ms": response.t_capture_ms, "rows": rows}
            self._latest_boxes = rows
            for row in rows:
                self._seen.append((t, row["cls"], row["kind"], row["sent"]))
                track = row["track_id"]
                if row["cls"] in VEHICLES and track:
                    history = self._tracks.setdefault(track, deque(maxlen=400))
                    history.append((t, row["closing_mps"]))
                    self._track_cls[track] = row["cls"]
                if row["kind"].startswith("alarm"):
                    self._alarm(track or "?", row, t)
            self._prune(t)

    def _alarm(self, track: str, row: dict[str, Any], t: int) -> None:
        """Open a row for a new approach, or update the one in progress."""
        open_row = self._open_alarm.get(track)
        if open_row is not None and t - open_row["t_last_ms"] <= ALARM_EPISODE_GAP_S * 1000:
            open_row.update(
                t_last_ms=t, frames=open_row["frames"] + 1, bearing_deg=row["bearing_deg"],
                range_band=row["range_band"], kind=row["kind"], sent=open_row["sent"] or row["sent"],
                closing_mps=row["closing_mps"],
                max_closing_mps=max(open_row["max_closing_mps"], row["closing_mps"] or 0.0),
            )
            return
        episode = dict(row, t_ms=t, t_last_ms=t, frames=1, max_closing_mps=row["closing_mps"] or 0.0, phone_said=[])
        self._open_alarm[track] = episode
        self._alarms.append(episode)

    def on_phone(self, kind: str, data: dict[str, Any]) -> None:
        """The phone's side: `hazard` (urgency) and `say` right after it."""
        t = now_ms()
        with self._lock:
            if kind == "hazard":
                self._phone.append({"t_ms": t, "kind": "hazard", "urgency": data.get("urgency")})
            elif kind == "say" and self._phone and self._phone[-1]["kind"] == "hazard" \
                    and t - self._phone[-1]["t_ms"] < 1500:
                self._phone[-1]["said"] = data.get("text")
                # To the approach in progress (the newest one still active).
                for alarm in reversed(self._alarms):
                    if t - alarm["t_last_ms"] < ALARM_EPISODE_GAP_S * 1000:
                        alarm["phone_said"].append(data.get("text"))
                        break

    def _prune(self, t: int) -> None:
        horizon = t - int(TRACK_HISTORY_S * 1000)
        for track in list(self._tracks):
            history = self._tracks[track]
            while history and history[0][0] < horizon:
                history.popleft()
            if not history:
                del self._tracks[track]
                self._track_cls.pop(track, None)
        while self._seen and self._seen[0][0] < t - 60_000:
            self._seen.popleft()

    # ------------------------------------------------------------- readers

    def state(self) -> dict[str, Any]:
        t = now_ms()
        with self._lock:
            recent = [r for r in self._responses if r > t - 5000]
            seen = list(self._seen)
            return {
                "active": self._latest is not None,
                "rules": {"vehicles": ["car", "bus"], "approach_mps": APPROACH_MPS, "stop_confidence": STOP_CONFIDENCE},
                "responses_per_s": round(len(recent) / 5.0, 1),
                "latest": self._latest,
                "latest_age_ms": None if self._latest is None else t - self._latest["t_ms"],
                "tracks": {
                    track: {"cls": self._track_cls.get(track), "points": [[p[0], p[1]] for p in history]}
                    for track, history in self._tracks.items()
                },
                "alarms": list(self._alarms),
                "phone": list(self._phone),
                "last_minute": {
                    "detections": dict(Counter(c for _, c, _, _ in seen)),
                    "alarms": sum(1 for a in self._alarms if a["t_last_ms"] > t - 60_000),
                    "sent": sum(1 for *_, s in seen if s),
                },
            }

    def view_jpeg(self, live_frame: Callable[[], np.ndarray | None] | None, max_age_s: float) -> bytes | None:
        """The detector's own crops (six level ring views + the floor view),
        with the latest boxes drawn: red alarm, amber vehicle, grey ignored.
        """
        if live_frame is None:
            return None
        if time.monotonic() - self._view_cache[0] < max_age_s:
            return self._view_cache[1]
        with self._render_lock:
            if time.monotonic() - self._view_cache[0] < max_age_s:
                return self._view_cache[1]
            jpeg = None
            try:
                frame = live_frame()
                if frame is not None:
                    with self._lock:
                        rows = list(self._latest_boxes)
                        fresh = self._latest is not None and now_ms() - self._latest["t_ms"] < 2000
                    jpeg = _detector_strip(frame, rows if fresh else [])
            except Exception:  # noqa: BLE001 - debug only
                jpeg = None
            self._view_cache = (time.monotonic(), jpeg)
            return jpeg


def _detector_strip(frame: np.ndarray, rows: list[dict[str, Any]]) -> bytes:
    from cane.bridge.dewarp import CROPSET_YAWS_DEG, NADIR_CROP_INDEX, dewarp_equirect

    crops = dewarp_equirect(frame, VIEW_CROP_PX, t_arrival_laptop_ms=0)
    tiles = [image.copy() for image in crops.images]
    for row in rows:
        index = row["crop_index"]
        if not 0 <= index < len(tiles):
            continue
        cx, cy, w, h = row["box"]
        s = VIEW_CROP_PX
        p0 = (int((cx - w / 2) * s), int((cy - h / 2) * s))
        p1 = (int((cx + w / 2) * s), int((cy + h / 2) * s))
        colour = _COLOURS.get(row["kind"], GREY)
        cv2.rectangle(tiles[index], p0, p1, colour, 3 if row["kind"].startswith("alarm") else 1)
        speed = "" if row["closing_mps"] is None else f" {row['closing_mps']:+.1f}"
        cv2.putText(tiles[index], f"{row['cls']}{speed}", (p0[0], max(12, p0[1] - 4)),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.42, colour, 1, cv2.LINE_AA)
    labelled = []
    for index, tile in enumerate(tiles):
        yaw = CROPSET_YAWS_DEG[index] if index < len(CROPSET_YAWS_DEG) else 0.0
        name = "floor" if index == NADIR_CROP_INDEX else (
            "ahead" if yaw == 0 else ("behind" if abs(yaw) == 180 else f"{abs(yaw):g} {'R' if yaw > 0 else 'L'}"))
        cv2.rectangle(tile, (0, 0), (VIEW_CROP_PX, 20), (0, 0, 0), -1)
        cv2.putText(tile, f"C{index} {name}", (6, 15), cv2.FONT_HERSHEY_SIMPLEX, 0.45, (255, 255, 255), 1, cv2.LINE_AA)
        labelled.append(tile)
    ok, buf = cv2.imencode(".jpg", np.hstack(labelled), [cv2.IMWRITE_JPEG_QUALITY, 70])
    return buf.tobytes() if ok else b""
