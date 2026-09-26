"""Equirect -> six 60-degree perspective crops, via our own numpy remap LUT.

Geometry (task-6-brief.md F3, device-responsibilities.md crop table):

| crop | yaw centre | covers |
|---|---|---|
| C0 | 0    | -30 .. +30, front (the phone's territory) |
| C1 | 60   | +30 .. +90, right front-quarter |
| C2 | 120  | +90 .. +150, right rear-quarter |
| C3 | 180  | +150 .. -150, rear -- wraps the +-180 seam |
| C4 | -120 | -150 .. -90, left rear-quarter |
| C5 | -60  | -90 .. -30, left front-quarter |

Equirect column 0 is bearing -180 and the centre column is bearing 0 (the
forward lens axis). Any mounting offset is absorbed by ``delta_deg`` when the
bearing is computed in ``events.py`` -- never by changing this file.

The remap tables depend only on (equirect W, equirect H, crop_px), so they are
built once by an ``lru_cache``\\ d builder and reused for every frame; the
per-frame cost is six ``cv2.remap`` calls. The idea is the prototype's
(~/Desktop/insta360 harness/projection.py); the implementation here is our own
numpy so the bridge carries no py360convert dependency.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from functools import lru_cache

import cv2
import numpy as np

CROP_YAWS_DEG: tuple[float, ...] = (0.0, 60.0, 120.0, 180.0, -120.0, -60.0)
CROP_FOV_DEG = 60.0
CROP_COUNT = len(CROP_YAWS_DEG)
EQUIRECT_ASPECT = 2.0
BGR_CHANNELS = 3
LUT_CACHE_SIZE = 8
_REMAP_FLAGS = cv2.INTER_LINEAR
_REMAP_BORDER = cv2.BORDER_WRAP


@dataclass(frozen=True, eq=False)
class CropLut:
    """The ``cv2.remap`` tables for one crop. ``eq=False``: ndarray equality."""

    yaw_deg: float
    map_x: np.ndarray
    map_y: np.ndarray


@dataclass(frozen=True, eq=False)
class CropSet:
    """Six perspective crops of one equirect frame, in C0..C5 order."""

    images: tuple[np.ndarray, ...]
    yaws_deg: tuple[float, ...]
    t_arrival_laptop_ms: int

    @property
    def crop_px(self) -> int:
        return int(self.images[0].shape[0])


def focal_px(crop_px: int) -> float:
    """Pinhole focal length for a square crop of ``CROP_FOV_DEG`` horizontal FOV."""
    return (crop_px / 2.0) / math.tan(math.radians(CROP_FOV_DEG / 2.0))


def _crop_lut(width: int, height: int, crop_px: int, yaw_deg: float) -> CropLut:
    """One crop's (map_x, map_y): output pixel -> equirect sample position."""
    focal = focal_px(crop_px)
    centre = crop_px / 2.0
    # Camera frame: +x right, +y down, +z forward along the crop axis.
    axis = np.arange(crop_px, dtype=np.float64) + 0.5 - centre
    x = axis[None, :]
    y = axis[:, None]
    yaw = math.radians(yaw_deg)
    # Rotate the ray about the vertical axis by the crop's yaw.
    world_x = x * math.cos(yaw) + focal * math.sin(yaw)
    world_z = -x * math.sin(yaw) + focal * math.cos(yaw)
    world_y = np.broadcast_to(y, (crop_px, crop_px))
    radius = np.sqrt(world_x**2 + world_y**2 + world_z**2)
    lon = np.arctan2(world_x, world_z)  # 0 = forward, + = right
    lat = np.arcsin(-world_y / radius)  # + = up
    map_x = (lon / (2.0 * math.pi) + 0.5) * width
    map_y = (0.5 - lat / math.pi) * height
    return CropLut(
        yaw_deg=yaw_deg,
        map_x=np.ascontiguousarray(np.broadcast_to(map_x, (crop_px, crop_px)), dtype=np.float32),
        map_y=np.ascontiguousarray(map_y, dtype=np.float32),
    )


@lru_cache(maxsize=LUT_CACHE_SIZE)
def build_remap_luts(width: int, height: int, crop_px: int) -> tuple[CropLut, ...]:
    """All six LUTs for one (frame size, crop size). Cached: built once per run."""
    if crop_px <= 0:
        raise ValueError(f"crop_px must be > 0, got {crop_px}")
    return tuple(_crop_lut(width, height, crop_px, yaw) for yaw in CROP_YAWS_DEG)


def _validate_equirect(image: np.ndarray) -> tuple[int, int]:
    if image.ndim != BGR_CHANNELS or image.shape[2] != BGR_CHANNELS:
        raise ValueError(f"dewarp needs an (H, W, 3) BGR frame, got shape {image.shape}")
    height, width = int(image.shape[0]), int(image.shape[1])
    if width != int(height * EQUIRECT_ASPECT):
        raise ValueError(
            f"dewarp needs a 2:1 equirect frame, got {width}x{height} "
            "(is the X4 in its 2880x1440 panorama mode?)"
        )
    return width, height


def dewarp_equirect(image: np.ndarray, crop_px: int, t_arrival_laptop_ms: int) -> CropSet:
    """Six ``crop_px`` square perspective crops covering the full 360 degrees."""
    width, height = _validate_equirect(image)
    luts = build_remap_luts(width, height, crop_px)
    crops = tuple(
        cv2.remap(image, lut.map_x, lut.map_y, _REMAP_FLAGS, borderMode=_REMAP_BORDER)
        for lut in luts
    )
    return CropSet(
        images=crops, yaws_deg=CROP_YAWS_DEG, t_arrival_laptop_ms=t_arrival_laptop_ms
    )


def encode_jpeg(image: np.ndarray, quality: int) -> bytes:
    """Encode one crop. Raises rather than returning an empty payload."""
    ok, buffer = cv2.imencode(".jpg", image, (cv2.IMWRITE_JPEG_QUALITY, quality))
    if not ok:
        raise RuntimeError(f"cv2.imencode failed for a {image.shape} crop at q{quality}")
    return bytes(buffer)


def encode_cropset(cropset: CropSet, quality: int) -> tuple[bytes, ...]:
    return tuple(encode_jpeg(image, quality) for image in cropset.images)
