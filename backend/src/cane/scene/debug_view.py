"""A local debug page for the scene server: http://localhost:8780.

Shows, live:
- the 360 camera as its six 60-degree views (what Gemini would be sent);
- every question: all the images sent, the one Gemini picked outlined, its
  box and x position drawn on it, the raw model reply, the answer, and what
  the phone then said and measured;
- what the phone heard and decided (transcripts, wake gate, intents);
- stats: link, 360 fps and frame age, Gemini latency and choices, the
  natural voice, TrueDepth, mic level.

Debug only: bound to 127.0.0.1 so camera images never leave the laptop, and
kept in memory (the last ``MAX_QUERIES`` questions). Nothing here is on the
answer path: a failure in it is logged and ignored.
"""

from __future__ import annotations

import base64
import json
import logging
import socketserver
import statistics
import threading
import time
from collections import Counter, deque
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable, Sequence

import cv2
import numpy as np

logger = logging.getLogger(__name__)

DEFAULT_PORT = 8780
MAX_QUERIES = 12
MAX_FEED = 80
#: The live 360 strip is re-cut at most this often for stats and stills.
LIVE_REFRESH_S = 0.8
#: The page's video stream (/live.mjpg). Each frame is six remaps + one JPEG,
#: a few ms; CANE_DEBUG_FPS lowers it if the laptop is busy (``full`` mode).
STREAM_FPS_ENV = "CANE_DEBUG_FPS"
DEFAULT_STREAM_FPS = 15.0
BOUNDARY = "canelive"
#: An unchanged frame is re-sent this often so the browser shows it.
STREAM_RESEND_S = 1.0
LIVE_CROP_PX = 240
PAGE = Path(__file__).with_name("debug_page.html")
#: Phone diagnostics shown in the feed (the rest only feed the stats).
FEED_KINDS = {
    "transcript", "gate", "intent", "say", "answer", "distance", "link", "link_alert", "note",
    "typed", "assist", "start", "hazard",
}
#: No phone diagnostic for this long -> shown as disconnected (it sends mic
#: levels every 2 s and depth every 1 s while walking).
PHONE_QUIET_MS = 4_000
#: "Guide my hand" stays on the page this long after its last update.
GUIDE_SHOW_MS = 45_000
#: The fingertip's recent path, drawn as a fading trail.
GUIDE_TRAIL = 40
GUIDE_TRAIL_MS = 1_500
GUIDE_FINAL_STATES = ("reached", "lost", "stopped")
#: A guide's ending stays on the page this long before the plain camera.
GUIDE_FINAL_HOLD_MS = 4_000

#: The page's signal yellow (#ffd200) and sign black, in BGR.
SIGN_YELLOW = (0, 210, 255)
SIGN_BLACK = (26, 26, 26)
GREY = (160, 160, 160)


def now_ms() -> int:
    return int(time.time() * 1000)


@dataclass
class ViewShot:
    label: str
    jpeg: bytes
    is_phone: bool


@dataclass
class QueryRecord:
    query_id: str
    t_ms: int
    kind: str
    target: str | None
    utterance: str
    views: list[ViewShot]
    reply: dict[str, Any] | None
    answer: dict[str, Any]
    error: str | None
    facts: str | None
    chosen: int | None
    #: Filled in later from the phone's own diagnostics.
    phone: dict[str, Any] = field(default_factory=dict)

    def summary(self) -> dict[str, Any]:
        return {
            "query_id": self.query_id, "t_ms": self.t_ms, "kind": self.kind, "target": self.target,
            "utterance": self.utterance, "labels": [v.label for v in self.views],
            "phone_views": [i for i, v in enumerate(self.views) if v.is_phone],
            "reply": self.reply, "answer": self.answer, "error": self.error, "facts": self.facts,
            "chosen": self.chosen, "phone": self.phone,
        }


class DebugHub:
    """Everything the page shows. Written from the event loop, the grab
    thread's consumers and the logging system; read by the HTTP threads.
    """

    def __init__(
        self,
        *,
        live_frame: Callable[[], np.ndarray | None] | None = None,
        frame_counter: Callable[[], int] | None = None,
        frame_arrival_ms: Callable[[], int | None] | None = None,
        source_name: str = "none",
        delta_deg: float = 0.0,
        clips_dir: Path | None = None,
        car_alerts: Any | None = None,
    ) -> None:
        from cane.scene.hazard_clips import HazardRecorder
        from cane.scene.hazard_view import HazardBoard

        #: The hazard section: filled by the bridge (``startcane full``).
        self.hazards = HazardBoard(delta_deg=delta_deg)
        #: Every car alarm, saved for replay (the alarm can't be staged live).
        self.clips = HazardRecorder(live_frame, clips_dir)
        #: The car-alert switch (``startcane full`` only): off, no vehicle is
        #: shown, alerted or saved.
        self.car_alerts = car_alerts
        self._lock = threading.Lock()
        self._live_frame = live_frame
        self._frame_counter = frame_counter
        self._frame_arrival_ms = frame_arrival_ms
        self._source_name = source_name
        self._started_ms = now_ms()
        self._queries: deque[QueryRecord] = deque(maxlen=MAX_QUERIES)
        self._feed: deque[dict[str, Any]] = deque(maxlen=MAX_FEED)
        self._phone_last_ms: int | None = None
        self._phone_link: bool | None = None
        self._depth: dict[str, Any] | None = None
        self._mic: float | None = None
        self._voice_sources: Counter[str] = Counter()
        self._voice_waits: deque[float] = deque(maxlen=50)
        self._link_alert: str | None = None
        self._live_cache: tuple[float, bytes | None, dict[str, Any]] = (0.0, None, {})
        #: One render at a time, shared by every viewer.
        self._render_lock = threading.Lock()
        self._live_source: np.ndarray | None = None
        self._fps_mark: tuple[float, int] | None = None
        self._fps = 0.0
        #: "Guide my hand": the phone's newest tracking update and frame.
        self._guide: dict[str, Any] | None = None
        self._guide_jpeg: bytes | None = None
        self._guide_frame_seq = 0
        self._guide_trail: deque[tuple[int, float, float]] = deque(maxlen=GUIDE_TRAIL)

    # ------------------------------------------------------------- writers

    def record_query(
        self,
        query: Any,
        views: Sequence[Any],
        reply: Any | None,
        answer: Any,
        *,
        error: str | None = None,
        facts: str | None = None,
    ) -> None:
        """Called by ``SceneQueryService`` after every answer (off the loop)."""
        try:
            reply_dict = reply.model_dump() if reply is not None else None
            chosen = reply_dict.get("image_index") if reply_dict else None
            if not (isinstance(chosen, int) and 0 <= chosen < len(views)) or answer.status != "found":
                chosen = None
            shots = [
                ViewShot(
                    label=v.image.label,
                    jpeg=_annotate(v.image.jpeg, reply_dict if i == chosen else None, answer.subject),
                    is_phone=bool(getattr(v, "is_phone", False)),
                )
                for i, v in enumerate(views)
            ]
            record = QueryRecord(
                query_id=query.query_id, t_ms=now_ms(), kind=query.kind, target=query.target,
                utterance=query.utterance, views=shots, reply=reply_dict, answer=answer.model_dump(),
                error=error, facts=facts, chosen=chosen,
            )
            with self._lock:
                self._queries.append(record)
        except Exception:  # noqa: BLE001 - debug view must never break answers
            logger.exception("debug view: could not record query")

    def record_hazards(self, response: Any, built: tuple[Any, ...], sent: tuple[Any, ...]) -> None:
        """The bridge's ``hazard_observer``: one inference response."""
        rows = self.hazards.record(response, built, sent)
        if self.car_alerts is None or self.car_alerts.on:
            self.clips.offer(now_ms(), rows)

    def set_car_alerts(self, on: bool) -> bool:
        """The page's switch. Off also drops a recording in progress."""
        if self.car_alerts is None:
            return False
        self.car_alerts.set(on)
        if not on:
            self.clips.discard()
        return True

    def hazard_jpeg(self, max_age_s: float = LIVE_REFRESH_S) -> bytes | None:
        return self.hazards.view_jpeg(self._live_frame, max_age_s)

    def on_phone_diag(self, record: dict[str, Any]) -> None:
        kind = record.get("kind")
        data = record.get("data") or {}
        received = now_ms()
        if kind in ("hazard", "say"):
            said = self.hazards.on_phone(str(kind), data)
            if said:
                self.clips.note_said(received, said)
        with self._lock:
            self._phone_last_ms = received
            if kind == "mic":
                self._mic = _num(data.get("peak"))
            elif kind == "depth":
                self._depth = dict(data, at_ms=received)
            elif kind == "link":
                self._phone_link = bool(data.get("connected"))
            elif kind == "link_alert":
                problem = data.get("problem")
                self._link_alert = None if problem in (None, "none") else str(problem)
            elif kind == "voice":
                self._voice_sources[str(data.get("source", "?"))] += 1
                waited = _num(data.get("waited_ms"))
                if waited is not None:
                    self._voice_waits.append(waited)
            if kind in ("answer", "distance", "say") and self._queries:
                self._attach_to_query(str(kind), dict(data, at_ms=received))
            if kind in FEED_KINDS and not (kind == "transcript" and not data.get("final")):
                self._feed.append({"t_ms": received, "kind": kind, "data": data})

    def on_phone_guide(self, update: Any) -> None:
        """One ``guide`` frame (a ``GuideUpdate``): the phone's chest camera,
        plus, during a guide, the thing's box, the fingertip and the gap.
        """
        received = now_ms()
        jpeg: bytes | None = None
        if update.frame is not None:
            try:
                jpeg = base64.b64decode(update.frame.jpeg_b64, validate=True)
            except (ValueError, TypeError):
                jpeg = None
        data = update.model_dump(exclude={"frame"})
        with self._lock:
            previous = self._guide
            if data["state"] == "idle" and previous is not None and previous["state"] in GUIDE_FINAL_STATES \
                    and received - previous["state_since_ms"] < GUIDE_FINAL_HOLD_MS:
                # "Got it" stays up a moment; only the picture moves on.
                data = dict(previous)
            if self._guide is None or self._guide.get("target") != data["target"] or data["state"] == "idle" \
                    or data["state"] == "find_hand" and self._guide.get("state") in ("reached", "lost", "stopped"):
                self._guide_trail.clear()
            if update.hand is not None:
                self._guide_trail.append((received, update.hand.x, update.hand.y))
            if jpeg:
                self._guide_jpeg = jpeg
                self._guide_frame_seq += 1
                data["frame_size"] = [update.frame.width, update.frame.height]
            elif self._guide is not None:
                data["frame_size"] = self._guide.get("frame_size")
            if self._guide is None or self._guide.get("state") != data["state"]:
                data["state_since_ms"] = received
            else:
                data["state_since_ms"] = self._guide.get("state_since_ms", received)
            data["received_ms"] = received
            self._guide = data

    def guide_jpeg(self) -> bytes | None:
        with self._lock:
            return self._guide_jpeg

    def guide_state(self) -> dict[str, Any] | None:
        """``/guide.json``: polled ~8 times a second while a guide runs."""
        with self._lock:
            return self._guide_state()

    def _guide_state(self) -> dict[str, Any] | None:
        """The page's view of the guide; None once it is long over."""
        if self._guide is None:
            return None
        now = now_ms()
        age = now - self._guide["received_ms"]
        if age > GUIDE_SHOW_MS:
            return None
        trail = [[round(x, 4), round(y, 4)] for t, x, y in self._guide_trail if now - t <= GUIDE_TRAIL_MS]
        return dict(self._guide, age_ms=age, frame_seq=self._guide_frame_seq, trail=trail)

    def _attach_to_query(self, kind: str, data: dict[str, Any]) -> None:
        """The phone's side of the latest question: what it said and measured."""
        latest = self._queries[-1]
        if kind == "answer" and data.get("query_id") != latest.query_id:
            return
        if kind == "say" and "said" in latest.phone:
            return  # only the answer itself, not later chatter
        if kind == "say" and "answer" not in latest.phone:
            return  # "Hold still..." comes before the answer
        latest.phone[{"say": "said", "answer": "answer", "distance": "distance"}[kind]] = data

    # ------------------------------------------------------------- readers

    def state(self) -> dict[str, Any]:
        live = self._live_stats()
        with self._lock:
            queries = [q.summary() for q in self._queries]
            answers = [q.answer for q in self._queries]
            latencies = [a["latency_ms"] for a in answers if a.get("latency_ms") is not None]
            phone_quiet = self._phone_last_ms is None or now_ms() - self._phone_last_ms > PHONE_QUIET_MS
            chosen = [q for q in self._queries if q.chosen is not None]
            return {
                "now_ms": now_ms(),
                "uptime_s": round((now_ms() - self._started_ms) / 1000),
                "phone": {
                    "connected": not phone_quiet,
                    "last_seen_ms": self._phone_last_ms,
                    "link_alert": self._link_alert,
                    "mic_peak": self._mic,
                    "depth": self._depth,
                },
                "x4": dict(live, tiles=live_order()),
                "gemini": {
                    "questions": len(answers),
                    "by_status": dict(Counter(a["status"] for a in answers)),
                    "latency_last_ms": latencies[-1] if latencies else None,
                    "latency_median_ms": round(statistics.median(latencies)) if latencies else None,
                    "latency_max_ms": max(latencies) if latencies else None,
                    "picked_phone": sum(1 for q in chosen if q.views[q.chosen].is_phone),  # type: ignore[index]
                    "picked_360": sum(1 for q in chosen if not q.views[q.chosen].is_phone),  # type: ignore[index]
                    "with_box": sum(1 for a in answers if a.get("phone_box")),
                },
                "voice": {
                    "by_source": dict(self._voice_sources),
                    "wait_median_ms": round(statistics.median(self._voice_waits)) if self._voice_waits else None,
                },
                "queries": queries,
                "feed": list(self._feed),
                "hazards": self.hazards.state(),
                "guide": self._guide_state(),
                "car_alerts": {"available": self.car_alerts is not None,
                               "on": self.car_alerts.on if self.car_alerts is not None else False},
            }

    def query_image(self, query_id: str, index: int) -> bytes | None:
        with self._lock:
            for q in self._queries:
                if q.query_id == query_id and 0 <= index < len(q.views):
                    return q.views[index].jpeg
        return None

    def live_jpeg(self, max_age_s: float = LIVE_REFRESH_S) -> bytes | None:
        self._refresh_live(max_age_s)
        return self._live_cache[1]

    @property
    def has_live(self) -> bool:
        return self._live_frame is not None

    def _live_stats(self) -> dict[str, Any]:
        self._refresh_live()
        stats = dict(self._live_cache[2])
        stats["source"] = self._source_name
        if self._frame_counter is not None:
            count = self._frame_counter()
            mark = self._fps_mark
            now = time.monotonic()
            if mark is None or now - mark[0] >= 2.0:
                if mark is not None:
                    self._fps = (count - mark[1]) / (now - mark[0])
                self._fps_mark = (now, count)
            stats["fps"] = round(self._fps, 1)
            stats["frames"] = count
        if self._frame_arrival_ms is not None:
            arrived = self._frame_arrival_ms()
            stats["frame_age_ms"] = None if arrived is None else now_ms() - arrived
        return stats

    def _refresh_live(self, max_age_s: float = LIVE_REFRESH_S) -> None:
        if self._live_frame is None or time.monotonic() - self._live_cache[0] < max_age_s:
            return
        with self._render_lock:
            # Another viewer may have rendered while this one waited.
            if time.monotonic() - self._live_cache[0] < max_age_s:
                return
            self._render_live()

    def _render_live(self) -> None:
        stats: dict[str, Any] = {}
        jpeg = None
        try:
            frame = self._live_frame()
            if frame is None:
                stats["problem"] = "no frame yet"
            elif frame is self._live_source and self._live_cache[1] is not None:
                # The same frame (a still photo, or the camera is slower than
                # the stream): keep the picture, skip the work.
                self._live_cache = (time.monotonic(), self._live_cache[1], self._live_cache[2])
                return
            else:
                self._live_source = frame
                stats["resolution"] = f"{frame.shape[1]}x{frame.shape[0]}"
                jpeg = _strip(frame)
        except Exception as exc:  # noqa: BLE001
            stats["problem"] = str(exc)[:200]
        self._live_cache = (time.monotonic(), jpeg, stats)


def _num(value: Any) -> float | None:
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else None


def _strip(equirect: np.ndarray) -> bytes:
    """The six views Gemini is sent (tall, tilted down), side by side, ordered
    left to right by yaw. The page labels them (``LIVE_ORDER``).
    """
    from cane.scene.tall_views import HEAD_PITCH_DEG, HEAD_VFOV_DEG, YAWS_DEG, tall_views

    images = tall_views(equirect, LIVE_CROP_PX, vfov_deg=HEAD_VFOV_DEG, pitch_deg=HEAD_PITCH_DEG)
    # Left of the wearer on the left: order the strip by yaw.
    order = sorted(range(len(images)), key=lambda i: _signed(YAWS_DEG[i]))
    ok, buf = cv2.imencode(".jpg", np.hstack([images[i] for i in order]), [cv2.IMWRITE_JPEG_QUALITY, 70])
    return buf.tobytes() if ok else b""


def _signed(yaw: float) -> float:
    return 180.0 if abs(yaw) == 180 else yaw


def yaw_name(yaw: float) -> str:
    """"Ahead", "60 L", "Behind": a view's short name on the page."""
    return "Ahead" if yaw == 0 else ("Behind" if abs(yaw) == 180 else f"{abs(yaw):g} {'R' if yaw > 0 else 'L'}")


def live_order() -> list[str]:
    """The live strip's tiles, left to right."""
    from cane.scene.tall_views import YAWS_DEG

    return [yaw_name(y) for y in sorted(YAWS_DEG, key=_signed)]


def _annotate(jpeg: bytes, reply: dict[str, Any] | None, subject: str | None) -> bytes:
    """Draw Gemini's box (signal yellow, black edge, subject on a black tag)
    and its x position (thin white line) on the image it picked."""
    if reply is None:
        return jpeg
    image = cv2.imdecode(np.frombuffer(jpeg, np.uint8), cv2.IMREAD_COLOR)
    if image is None:
        return jpeg
    h, w = image.shape[:2]
    thick = max(2, w // 240)
    box = reply.get("box_2d")
    if isinstance(box, list) and len(box) == 4:
        y0, x0, y1, x1 = (float(v) / 1000 for v in box)
        p0 = (int(min(x0, x1) * w), int(min(y0, y1) * h))
        p1 = (int(max(x0, x1) * w), int(max(y0, y1) * h))
        cv2.rectangle(image, p0, p1, SIGN_BLACK, thick + 2)
        cv2.rectangle(image, p0, p1, SIGN_YELLOW, thick)
        if subject:
            scale = max(0.5, w / 900)
            weight = max(1, thick // 2)
            (tw, th), base = cv2.getTextSize(subject, cv2.FONT_HERSHEY_SIMPLEX, scale, weight)
            top = p0[1] - th - base - 8 if p0[1] - th - base - 8 >= 0 else p1[1]
            cv2.rectangle(image, (p0[0], top), (p0[0] + tw + 10, top + th + base + 8), SIGN_BLACK, -1)
            cv2.putText(image, subject, (p0[0] + 5, top + th + 4), cv2.FONT_HERSHEY_SIMPLEX,
                        scale, SIGN_YELLOW, weight, cv2.LINE_AA)
    x = reply.get("x_center")
    if isinstance(x, (int, float)):
        fx = x / 1000 if 1 < x <= 1000 else x
        px = int(min(max(fx, 0.0), 1.0) * (w - 1))
        cv2.line(image, (px, 0), (px, h), (255, 255, 255), 1)
    ok, buf = cv2.imencode(".jpg", image, [cv2.IMWRITE_JPEG_QUALITY, 85])
    return buf.tobytes() if ok else jpeg


class PhoneDiagHandler(logging.Handler):
    """Feeds the phone's black-box lines (logged as JSON on ``cane.phone``)
    into the hub, so the page needs no change to the server.
    """

    def __init__(self, hub: DebugHub) -> None:
        super().__init__(level=logging.INFO)
        self._hub = hub

    def emit(self, record: logging.LogRecord) -> None:
        try:
            parsed = json.loads(record.getMessage())
        except (ValueError, TypeError):
            return
        if isinstance(parsed, dict):
            self._hub.on_phone_diag(parsed)


def stream_fps() -> float:
    import os

    try:
        return min(max(float(os.environ.get(STREAM_FPS_ENV, DEFAULT_STREAM_FPS)), 1.0), 30.0)
    except ValueError:
        return DEFAULT_STREAM_FPS


class _QuickServer(ThreadingHTTPServer):
    """``HTTPServer.server_bind`` looks up the machine's full DNS name, which
    took 35 s on the venue network; the page never uses it.
    """

    def server_bind(self) -> None:
        socketserver.TCPServer.server_bind(self)
        host, port = self.server_address[:2]
        self.server_name, self.server_port = str(host), int(port)


def serve_debug(hub: DebugHub, port: int = DEFAULT_PORT, host: str = "127.0.0.1") -> ThreadingHTTPServer:
    """Start the page on a daemon thread; returns the server (``.shutdown()``)."""

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802 - http.server API
            path = self.path.split("?", 1)[0]
            try:
                if path == "/":
                    self._send(200, "text/html; charset=utf-8", PAGE.read_bytes())
                elif path == "/state.json":
                    self._send(200, "application/json", json.dumps(hub.state()).encode())
                elif path == "/live.mjpg":
                    self._stream(hub.live_jpeg)
                elif path == "/hazards.mjpg":
                    self._stream(hub.hazard_jpeg)
                elif path == "/hazards.jpg":
                    jpeg = hub.hazard_jpeg()
                    self._send(200 if jpeg else 404, "image/jpeg", jpeg or b"")
                elif path == "/guide.json":
                    self._send(200, "application/json", json.dumps(hub.guide_state()).encode())
                elif path == "/guide.jpg":
                    jpeg = hub.guide_jpeg()
                    self._send(200 if jpeg else 404, "image/jpeg", jpeg or b"")
                elif path == "/live.jpg":
                    jpeg = hub.live_jpeg()
                    self._send(200 if jpeg else 404, "image/jpeg", jpeg or b"")
                elif path == "/clips.json":
                    from cane.scene.hazard_clips import list_clips

                    self._send(200, "application/json", json.dumps(list_clips(hub.clips.directory)).encode())
                elif path.startswith("/clips/"):
                    from cane.scene.hazard_clips import clip_file

                    _, _, name, file = path.split("/", 3)
                    body = clip_file(name, file, hub.clips.directory)
                    kind = "application/json" if file.endswith(".json") else "image/jpeg"
                    self._send(200 if body else 404, kind if body else "text/plain", body or b"not found")
                elif path.startswith("/query/"):
                    _, _, query_id, name = path.split("/", 3)
                    jpeg = hub.query_image(query_id, int(name.removesuffix(".jpg")))
                    self._send(200 if jpeg else 404, "image/jpeg", jpeg or b"")
                else:
                    self._send(404, "text/plain", b"not found")
            except (ValueError, BrokenPipeError, ConnectionResetError):
                pass

        def _stream(self, frames: Callable[..., bytes | None]) -> None:
            """Motion JPEG: the browser shows each part as it arrives."""
            if not hub.has_live:
                self._send(404, "text/plain", b"no live camera")
                return
            period = 1.0 / stream_fps()
            self.send_response(200)
            self.send_header("Content-Type", f"multipart/x-mixed-replace; boundary={BOUNDARY}")
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            last: bytes | None = None
            sent_at = 0.0
            while True:
                started = time.monotonic()
                jpeg = frames(max_age_s=period * 0.9)
                # Chrome shows a part only when the next one starts, so an
                # unchanged picture (a still photo) is re-sent every second.
                if jpeg and (jpeg is not last or started - sent_at >= STREAM_RESEND_S):
                    self.wfile.write(
                        f"--{BOUNDARY}\r\nContent-Type: image/jpeg\r\nContent-Length: {len(jpeg)}\r\n\r\n".encode()
                    )
                    self.wfile.write(jpeg)
                    self.wfile.write(b"\r\n")
                    self.wfile.flush()
                    last, sent_at = jpeg, started
                time.sleep(max(0.0, period - (time.monotonic() - started)))

        def do_POST(self) -> None:  # noqa: N802 - http.server API
            """Only ``/car-alerts``. A JSON body forces a browser preflight, so
            no other web page can flip the switch behind the operator's back.
            """
            host = (self.headers.get("Host") or "").split(":", 1)[0]
            if self.path != "/car-alerts" or host not in ("127.0.0.1", "localhost") \
                    or (self.headers.get("Content-Type") or "").split(";")[0].strip() != "application/json":
                self._send(403, "text/plain", b"forbidden")
                return
            try:
                length = min(int(self.headers.get("Content-Length") or 0), 1_000)
                on = json.loads(self.rfile.read(length) or b"{}").get("on")
            except (ValueError, AttributeError):
                on = None
            if not isinstance(on, bool):
                self._send(400, "text/plain", b"send {\"on\": true|false}")
                return
            if not hub.set_car_alerts(on):
                self._send(409, "text/plain", b"car alerts run only with ./startcane full")
                return
            self._send(200, "application/json", json.dumps({"on": on}).encode())

        def _send(self, status: int, content_type: str, body: bytes) -> None:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args: object) -> None:  # quiet: the page polls every second
            return

    server = _QuickServer((host, port), Handler)
    server.daemon_threads = True
    threading.Thread(target=server.serve_forever, name="debug-view", daemon=True).start()
    logger.info("debug view on http://localhost:%d", server.server_address[1])
    return server


PORT_ENV = "CANE_DEBUG_PORT"


def start_debug_view(
    *, grab: Any | None = None, still: np.ndarray | None = None, port: int | None = None,
    delta_deg: float = 0.0,
    car_alerts: Any | None = None,
) -> DebugHub | None:
    """The page for one run: live frames from ``grab`` (a ``GrabThread``) or
    one ``still`` image, the phone's diagnostics from the ``cane.phone``
    logger. ``CANE_DEBUG_PORT`` (default 8780; 0 = off). Never fatal.
    """
    import os

    if port is None:
        try:
            port = int(os.environ.get(PORT_ENV, DEFAULT_PORT))
        except ValueError:
            port = DEFAULT_PORT
    if port <= 0:
        return None
    if grab is not None:
        def newest() -> Any | None:
            return grab.wait_for_new(0, 0.05)

        hub = DebugHub(
            live_frame=lambda: (f.image if (f := newest()) is not None else None),
            frame_counter=lambda: grab.frames_grabbed,
            frame_arrival_ms=lambda: (f.t_arrival_laptop_ms if (f := newest()) is not None else None),
            source_name=str(grab.source_name),
            delta_deg=delta_deg,
            car_alerts=car_alerts,
        )
    elif still is not None:
        hub = DebugHub(live_frame=lambda: still, source_name="still test photo")
    else:
        hub = DebugHub()
    try:
        serve_debug(hub, port)
    except OSError as exc:
        logger.warning("debug view: port %d unavailable (%s); running without it", port, exc)
        return None
    logging.getLogger("cane.phone").addHandler(PhoneDiagHandler(hub))
    return hub
