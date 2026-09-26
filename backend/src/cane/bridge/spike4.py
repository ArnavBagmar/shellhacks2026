"""Spike 4 (design Sec 4.3): measure the pipeline constant D, by eye.

D = t_arrival_laptop - t_displayed: how far behind reality a frame already is
by the time it reaches us. There is no OCR here on purpose -- the method is:

1. Run this with the X4 pointed at the laptop screen:
   ``PYTHONPATH=src uv run python -m cane.bridge.spike4 --source x4``
2. The window shows the live frame with the *arrival* stamp burned in, beside
   a free-running millisecond counter. Photograph or screenshot the window.
3. Read both numbers off the image: their difference is D for that frame.
   Repeat ~10 times and take the median; feed it to ``--pipeline-delay-ms``.

Not a tested component: it is documentation of a measurement method.
"""

from __future__ import annotations

import argparse
import logging
import sys
from typing import Sequence

import cv2

from cane.bridge.capture.grab_thread import GrabThread
from cane.transport.wall_clock import now_ms

logger = logging.getLogger("cane.bridge.spike4")

WINDOW_NAME = "cane spike 4 -- read both numbers"
DISPLAY_WIDTH = 1280
FRAME_TIMEOUT_S = 5.0
QUIT_KEYS = (ord("q"), 27)
_FONT = cv2.FONT_HERSHEY_SIMPLEX


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="python -m cane.bridge.spike4")
    parser.add_argument("--source", default="x4", help="synthetic | file:PATH | index:N | x4")
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:  # pragma: no cover - a GUI loop
    from cane.bridge.__main__ import LOG_FORMAT, open_source

    args = parse_args(argv)
    logging.basicConfig(level=logging.INFO, format=LOG_FORMAT, stream=sys.stderr)
    grab = GrabThread(open_source(args.source))
    grab.start()
    last_seq = 0
    try:
        while True:
            frame = grab.wait_for_new(last_seq, FRAME_TIMEOUT_S)
            if frame is None:
                logger.error("no frame within %.0fs; is the camera still attached?", FRAME_TIMEOUT_S)
                return 1
            last_seq = frame.seq
            scale = DISPLAY_WIDTH / frame.image.shape[1]
            view = cv2.resize(frame.image, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA)
            age_ms = now_ms() - frame.t_arrival_laptop_ms
            cv2.putText(view, f"now  {now_ms() % 100000}", (20, 50), _FONT, 1.2, (0, 255, 255), 3)
            cv2.putText(
                view,
                f"arrival {frame.t_arrival_laptop_ms % 100000} (+{age_ms}ms to display)",
                (20, 100), _FONT, 1.0, (0, 255, 255), 2,
            )
            cv2.imshow(WINDOW_NAME, view)
            if cv2.waitKey(1) & 0xFF in QUIT_KEYS:
                return 0
    finally:
        grab.stop()
        cv2.destroyAllWindows()


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
