"""Walk a building with one camera and see whether the signs get read.

A manual harness, in the spirit of ``bridge/spike4.py``: small, untested, and
documentation of a method rather than a component. It exists to answer the two
questions the unit tests cannot, before the X4 and the phone app are involved:

1. does Gemini actually read this building's signage off a real frame?
2. does the memory accumulate the places, and does a spoken target match them?

Run it with an iPhone as the Mac's webcam (Continuity Camera), carry the
laptop, and walk::

    cd backend
    uv run --extra scene python -m cane.scene.walktest --camera 1

Then ask it things over HTTP, from anywhere on the machine::

    curl 'localhost:8787/find?target=restroom'
    curl localhost:8787/places
    curl localhost:8787/healthz

**What one forward camera cannot show you.** The ring's whole point is the
sign beside and behind the wearer, and a single 70-degree lens has neither. A
sign you walk past is simply never seen again, so its stored bearing goes
stale rather than migrating rearward -- which is exactly when
``PlaceMemory`` withholds the angle and says "direction unclear" instead. That
is the honest behaviour, not a bug in the harness, and it is the one beat that
needs the X4 to demonstrate properly.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Sequence
from urllib.parse import parse_qs, urlparse

from cane.bridge.capture.grab_thread import GrabThread
from cane.bridge.capture.opencv_source import open_index
from cane.bridge.dewarp import encode_jpeg
from cane.scene.frames import GrabProvider
from cane.scene.place_names import spoken_subject
from cane.scene.places import PlaceMemory, Recall
from cane.scene.service import View
from cane.scene.signscan import SignScanner, scan_interval_s
from cane.scene.vlm import GeminiVLM, LabeledImage, VlmUnavailableError

logger = logging.getLogger("cane.walktest")

DEFAULT_CAMERA = 1
DEFAULT_PORT = 8787
#: An iPhone's wide lens is about 70 degrees horizontally, the same figure the
#: phone app sends with its own photos.
DEFAULT_HFOV_DEG = 70.0
JPEG_QUALITY = 80
#: One image, straight ahead. The scan prompt asks for an ``image_index`` and
#: an ``x_center``; with a single view the index is always 0.
VIEW_LABEL = "IMAGE 0: the view straight ahead of the wearer."


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="python -m cane.scene.walktest", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--camera", type=int, default=DEFAULT_CAMERA,
                        help="OpenCV camera index (0 is usually the built-in, 1 the iPhone)")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--hfov-deg", type=float, default=DEFAULT_HFOV_DEG)
    parser.add_argument("--interval-s", type=float, default=None,
                        help="seconds between sweeps (default: CANE_SIGN_SCAN_INTERVAL_S, else 4)")
    parser.add_argument("--log-level", default="INFO")
    return parser.parse_args(argv)


# ------------------------------------------------------------------ speaking


def sector_words(bearing_deg: float) -> str:
    """The five sectors the phone speaks, so this harness and the wearer's
    device describe a direction with the same words.

    Mirrors ``CaneOutput/BearingSector.swift``. Duplicated rather than shared
    because this is a throwaway harness and that file is the one place the
    product is allowed to turn a sign into a side.
    """
    size = abs(bearing_deg)
    side = "right" if bearing_deg > 0 else "left"
    if size < 15:
        return "straight ahead"
    if size < 60:
        return f"ahead to the {side}"
    if size < 120:
        return f"to your {side}"
    if size <= 165:
        return f"behind you to the {side}"
    return "directly behind you"


def age_words(age_ms: int) -> str | None:
    """"seen 15 seconds ago", or nothing when it was seen just now."""
    if age_ms < 5_000:
        return None
    seconds = round(age_ms / 1000)
    if seconds < 60:
        return f"seen {seconds} seconds ago"
    minutes = round(seconds / 60)
    return f"seen about {minutes} minute{'s' if minutes != 1 else ''} ago"


def sentence_for(recalled: Recall) -> str:
    """What the phone would say about this memory."""
    subject = spoken_subject(recalled.sighting.text)
    age = age_words(recalled.age_ms)
    tail = f", {age}" if age else ""
    if recalled.bearing_deg is None:
        return f"{subject.capitalize()} nearby, direction unclear{tail}."
    return f"{sector_words(recalled.bearing_deg).capitalize()}, {subject}{tail}."


def not_yet(target: str) -> str:
    return f"Not yet. Keep walking and I'll tell you when I see a {target}."


# ------------------------------------------------------------------ the walk


class Walk:
    """One camera, one memory, and a scanner sweeping between them."""

    def __init__(self, memory: PlaceMemory, scanner: SignScanner) -> None:
        self.memory = memory
        self.scanner = scanner

    def find(self, target: str) -> dict[str, object]:
        recalled = self.memory.recall(target)
        if recalled is None:
            return {"status": "not_found", "sentence": not_yet(target), "bearing_deg": None, "age_ms": None}
        return {
            "status": "found",
            "sentence": sentence_for(recalled),
            "sign": recalled.sighting.text,
            "bearing_deg": recalled.bearing_deg,
            "age_ms": recalled.age_ms,
            "seen_count": recalled.sighting.seen_count,
        }

    def places(self) -> list[dict[str, object]]:
        return [
            {
                "text": s.text,
                "kind": s.kind.value,
                "bearing_deg": s.bearing_deg,
                "confidence": s.confidence,
                "seen_count": s.seen_count,
            }
            for s in self.memory.places()
        ]


def _handler(walk: Walk) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def do_GET(self) -> None:  # noqa: N802 - stdlib's spelling
            route = urlparse(self.path)
            if route.path == "/healthz":
                return self._json({"ok": True, "indoor": walk.scanner.indoor,
                                   "places": len(walk.memory.places())})
            if route.path == "/places":
                return self._json(walk.places())
            if route.path == "/find":
                targets = parse_qs(route.query).get("target", [])
                target = targets[0].strip() if targets else ""
                if not target:
                    return self._json({"error": "pass ?target=restroom"}, status=400)
                answer = walk.find(target)
                logger.info("asked for %r -> %s", target, answer["sentence"])
                return self._json(answer)
            self._json({"error": "try /find?target=..., /places or /healthz"}, status=404)

        def _json(self, payload: object, status: int = 200) -> None:
            body = json.dumps(payload, indent=2).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *_: object) -> None:
            """Quiet: the interesting lines are the scanner's own."""

    return Handler


async def run(args: argparse.Namespace) -> int:
    try:
        # One unhedged call per sweep: nobody is waiting on a sweep, so paying
        # two or three times for every one would be spending quota on
        # impatience nobody feels.
        vlm = GeminiVLM.from_env()
    except VlmUnavailableError as exc:
        logger.error("%s -- put GEMINI_API_KEY in backend/.env", exc)
        return 2

    source = open_index(args.camera)
    grab = GrabThread(source)
    grab.start()
    frames = GrabProvider(grab)
    memory = PlaceMemory()

    async def views() -> tuple[View, ...]:
        image = await frames.fresh_equirect()
        if image is None:
            return ()
        jpeg = await asyncio.to_thread(encode_jpeg, image, JPEG_QUALITY)
        return (
            View(image=LabeledImage(VIEW_LABEL, jpeg), centre_deg=0.0, fov_deg=args.hfov_deg),
        )

    scanner = SignScanner(
        vlm=vlm,
        views=views,
        memory=memory,
        interval_s=args.interval_s if args.interval_s is not None else scan_interval_s(),
    )
    walk = Walk(memory, scanner)

    server = ThreadingHTTPServer(("127.0.0.1", args.port), _handler(walk))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    logger.info(
        "walking on camera %d, asking on http://127.0.0.1:%d "
        "-- try: curl 'localhost:%d/find?target=restroom'",
        args.camera, args.port, args.port,
    )
    await vlm.warm_up()
    try:
        await scanner.run()
    except asyncio.CancelledError:
        pass
    finally:
        server.shutdown()
        grab.stop()
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    logging.basicConfig(level=args.log_level, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    try:
        return asyncio.run(run(args))
    except KeyboardInterrupt:
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
