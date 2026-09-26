"""Save one live six-crop contact sheet -- the visual proof of the geometry.

    PYTHONPATH=src uv run python -m cane.bridge.snapshot --source x4 --out DIR

Writes ``step-06-live-crops.jpg`` (six crops tiled 3x2 with the crop index and
yaw burned in) and ``step-06-live-equirect.jpg`` (a 1440x720 copy of the
source frame). Both are documentation artefacts, not a runtime path.
"""

from __future__ import annotations

import argparse
import logging
import sys
import time
from pathlib import Path
from typing import Sequence

import cv2
import numpy as np

from cane.bridge.capture.grab_thread import GrabThread
from cane.bridge.dewarp import CropSet, dewarp_equirect

logger = logging.getLogger("cane.bridge.snapshot")

CROPS_FILENAME = "step-06-live-crops.jpg"
EQUIRECT_FILENAME = "step-06-live-equirect.jpg"
CONTACT_SHEET_COLS = 3
CONTACT_SHEET_ROWS = 2
EQUIRECT_PREVIEW_SIZE = (1440, 720)
DEFAULT_CROP_PX = 640
DEFAULT_QUALITY = 60
MAX_SHEET_BYTES = 400 * 1024
SHRINK_FACTOR = 0.8
MAX_SHRINK_STEPS = 6
WARMUP_S = 1.5
FRAME_TIMEOUT_S = 10.0
_LABEL_ORIGIN = (12, 34)
_LABEL_FONT_SCALE_PER_PX = 1.0 / 800.0
_LABEL_SHADOW = (0, 0, 0)
_LABEL_COLOUR = (0, 255, 255)


def build_contact_sheet(cropset: CropSet) -> np.ndarray:
    """Six crops tiled 3x2, each labelled ``C<i> yaw <deg>``."""
    labelled = [
        _label(crop.copy(), f"C{i} yaw {cropset.yaws_deg[i]:+.0f}")
        for i, crop in enumerate(cropset.images)
    ]
    rows = [
        np.concatenate(labelled[row * CONTACT_SHEET_COLS : (row + 1) * CONTACT_SHEET_COLS], axis=1)
        for row in range(CONTACT_SHEET_ROWS)
    ]
    return np.concatenate(rows, axis=0)


def _label(image: np.ndarray, text: str) -> np.ndarray:
    scale = max(image.shape[0] * _LABEL_FONT_SCALE_PER_PX, 0.4)
    for colour, thickness in ((_LABEL_SHADOW, 5), (_LABEL_COLOUR, 2)):
        cv2.putText(
            image, text, _LABEL_ORIGIN, cv2.FONT_HERSHEY_SIMPLEX, scale, colour, thickness,
            cv2.LINE_AA,
        )
    return image


def encode_under_cap(image: np.ndarray, quality: int, max_bytes: int) -> bytes:
    """JPEG at ``quality``, shrinking the image until it fits ``max_bytes``."""
    current = image
    for step in range(MAX_SHRINK_STEPS):
        ok, buffer = cv2.imencode(".jpg", current, (cv2.IMWRITE_JPEG_QUALITY, quality))
        if not ok:
            raise RuntimeError(f"cv2.imencode failed for a {current.shape} image")
        data = bytes(buffer)
        if len(data) <= max_bytes:
            return data
        scale = SHRINK_FACTOR ** (step + 1)
        logger.info("contact sheet is %d bytes; retrying at %.0f%%", len(data), scale * 100)
        current = cv2.resize(image, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA)
    raise RuntimeError(f"could not get the contact sheet under {max_bytes} bytes")


def write_snapshot(
    equirect: np.ndarray,
    out_dir: Path,
    crop_px: int = DEFAULT_CROP_PX,
    quality: int = DEFAULT_QUALITY,
) -> tuple[Path, ...]:
    """Write both artefacts and return their paths."""
    out_dir.mkdir(parents=True, exist_ok=True)
    cropset = dewarp_equirect(equirect, crop_px, 0)
    sheet_path = out_dir / CROPS_FILENAME
    sheet_path.write_bytes(encode_under_cap(build_contact_sheet(cropset), quality, MAX_SHEET_BYTES))
    preview = cv2.resize(equirect, EQUIRECT_PREVIEW_SIZE, interpolation=cv2.INTER_AREA)
    equirect_path = out_dir / EQUIRECT_FILENAME
    ok, buffer = cv2.imencode(".jpg", preview, (cv2.IMWRITE_JPEG_QUALITY, quality))
    if not ok:  # pragma: no cover - imencode only fails on a malformed array
        raise RuntimeError("cv2.imencode failed for the equirect preview")
    equirect_path.write_bytes(bytes(buffer))
    return (sheet_path, equirect_path)


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="python -m cane.bridge.snapshot")
    parser.add_argument("--source", default="x4", help="synthetic | file:PATH | index:N | x4")
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--crop-px", type=int, default=DEFAULT_CROP_PX)
    parser.add_argument("--quality", type=int, default=DEFAULT_QUALITY)
    parser.add_argument("--warmup-s", type=float, default=WARMUP_S)
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:  # pragma: no cover - needs a camera
    from cane.bridge.__main__ import LOG_FORMAT, open_source

    args = parse_args(argv)
    logging.basicConfig(level=logging.INFO, format=LOG_FORMAT, stream=sys.stderr)
    grab = GrabThread(open_source(args.source))
    grab.start()
    try:
        time.sleep(args.warmup_s)  # let auto-exposure settle
        frame = grab.wait_for_new(0, FRAME_TIMEOUT_S)
        if frame is None:
            logger.error("no frame from %s within %.0fs", args.source, FRAME_TIMEOUT_S)
            return 1
        paths = write_snapshot(frame.image, args.out, args.crop_px, args.quality)
    finally:
        grab.stop()
    for path in paths:
        logger.info("wrote %s (%d bytes)", path, path.stat().st_size)
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
