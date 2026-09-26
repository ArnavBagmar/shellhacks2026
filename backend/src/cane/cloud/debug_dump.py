"""Annotated contact sheets for the live acceptance run. Debug only.

Off unless `CANE_DUMP_DIR` is set, written every `CANE_DUMP_EVERY_N` frames,
and always *after* `inference_ms` has been measured, so switching it on can
never move the number step 8 and step 16 read.

Clock-free like the rest of the package: sheets are numbered by a frame
counter, never stamped with a local time.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Final, Sequence

import cv2
import numpy as np
from numpy.typing import NDArray

from cane.cloud.api_models import InferenceResponse

logger = logging.getLogger(__name__)

TILE_PX: Final = 400
COLUMNS: Final = 3
BOX_COLOUR: Final = (0, 255, 0)
TEXT_COLOUR: Final = (0, 0, 0)
BOX_THICKNESS: Final = 2
FONT: Final = cv2.FONT_HERSHEY_SIMPLEX
FONT_SCALE: Final = 0.42
START_QUALITY: Final = 60
MIN_QUALITY: Final = 20
QUALITY_STEP: Final = 10
MAX_BYTES: Final = 400_000


def _annotate(tile: NDArray[np.uint8], response: InferenceResponse, crop_index: int) -> None:
    height, width = tile.shape[:2]
    for detection in response.detections:
        if detection.crop_index != crop_index:
            continue
        half_w, half_h = detection.w * width / 2.0, detection.h * height / 2.0
        x1 = int(detection.cx * width - half_w)
        y1 = int(detection.cy * height - half_h)
        x2 = int(detection.cx * width + half_w)
        y2 = int(detection.cy * height + half_h)
        cv2.rectangle(tile, (x1, y1), (x2, y2), BOX_COLOUR, BOX_THICKNESS)
        label = f"{detection.cls.value} {detection.confidence:.2f} {detection.track_id}"
        if detection.closing_mps is not None:
            label += f" {detection.closing_mps:+.1f}m/s"
        cv2.rectangle(tile, (x1, max(y1 - 16, 0)), (x1 + 8 * len(label), y1), BOX_COLOUR, -1)
        cv2.putText(tile, label, (x1 + 2, max(y1 - 4, 10)), FONT, FONT_SCALE, TEXT_COLOUR, 1)


def build_sheet(
    images: Sequence[NDArray[np.uint8]], response: InferenceResponse
) -> NDArray[np.uint8]:
    """Six crops, boxes drawn, laid out 3 x 2 in crop order."""
    tiles = []
    for crop_index, image in enumerate(images):
        tile = cv2.resize(image, (TILE_PX, TILE_PX), interpolation=cv2.INTER_AREA).copy()
        _annotate(tile, response, crop_index)
        cv2.putText(tile, f"crop {crop_index}", (6, 18), FONT, 0.5, BOX_COLOUR, 1)
        tiles.append(tile)
    rows = [
        np.hstack(tiles[start : start + COLUMNS]) for start in range(0, len(tiles), COLUMNS)
    ]
    return np.vstack(rows)


def write_contact_sheet(
    dump_dir: Path, images: Sequence[NDArray[np.uint8]], response: InferenceResponse, index: int
) -> Path:
    """Write one sheet, shrinking quality until it fits `MAX_BYTES`."""
    dump_dir.mkdir(parents=True, exist_ok=True)
    sheet = build_sheet(images, response)
    quality = START_QUALITY
    encoded = b""
    while quality >= MIN_QUALITY:
        ok, buffer = cv2.imencode(".jpg", sheet, [int(cv2.IMWRITE_JPEG_QUALITY), quality])
        if not ok:
            raise RuntimeError("could not JPEG-encode the contact sheet")
        encoded = bytes(buffer)
        if len(encoded) <= MAX_BYTES:
            break
        quality -= QUALITY_STEP
    path = dump_dir / f"sheet-{index:06d}.jpg"
    path.write_bytes(encoded)
    return path


def maybe_write_sheet(
    dump_dir: Path | None,
    every_n: int,
    frames_seen: int,
    images: Sequence[NDArray[np.uint8]],
    response: InferenceResponse,
) -> Path | None:
    """The whole on/off decision, so the request handler carries none of it.

    Never fatal: a debug sheet that fails to write is not a failed request.
    """
    if dump_dir is None or frames_seen % every_n:
        return None
    try:
        path = write_contact_sheet(dump_dir, images, response, frames_seen)
    except Exception:  # noqa: BLE001 - diagnostics must never break serving
        logger.exception("debug contact sheet failed")
        return None
    logger.info("wrote debug contact sheet %s", path)
    return path
