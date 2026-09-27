"""Equirect -> six 60-degree ring crops plus one nadir crop, via our own numpy remap LUT.

Geometry (task-6-brief.md F3, device-responsibilities.md crop table, and the
x4-sphere-coverage PRD for C6):

| crop | yaw centre | covers |
|---|---|---|
| C0 | 0    | -30 .. +30, front (the phone's territory) |
| C1 | 60   | +30 .. +90, right front-quarter |
| C2 | 120  | +90 .. +150, right rear-quarter |
| C3 | 180  | +150 .. -150, rear -- wraps the +-180 seam |
| C4 | -120 | -150 .. -90, left rear-quarter |
| C5 | -60  | -90 .. -30, left front-quarter |
| C6 | nadir | every bearing, latitude -30 .. -90 (the floor out to 2.4 m); wearer masked |

The six ring crops are square 60-degree pinholes at zero pitch, so they see
latitude -30 .. +30 and nothing else. C6 is a 120-degree pinhole looking
straight down with image-up = forward and image-right = the wearer's right:
off-nadir angle theta lands at radius ``focal * tan(theta)`` from the crop
centre, so it meets the ring exactly at -30 and reaches the nadir. The disc
within ``NADIR_BODY_MASK_DEG`` of the nadir is the wearer's own shoulder and
torso and is blacked out before encoding.

Equirect column 0 is bearing -180 and the centre column is bearing 0 (the
forward lens axis). Any mounting offset is absorbed by ``delta_deg`` when the
bearing is computed in ``events.py`` -- never by changing this file.

The remap tables depend only on (equirect W, equirect H, crop_px), so they are
built once by an ``lru_cache``\\ d builder and reused for every frame; the
per-frame cost is seven ``cv2.remap`` calls. The idea is the prototype's
(~/Desktop/insta360 harness/projection.py); the implementation here is our own
numpy so the bridge carries no py360convert dependency.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from functools import lru_cache

import cv2
import numpy as np

#: The six ring crops, C0..C5. Unchanged by C6: every consumer of ring
#: geometry (bearing math, the 48-column gap ring, the VLM prompt) keys on it.
CROP_YAWS_DEG: tuple[float, ...] = (0.0, 60.0, 120.0, 180.0, -120.0, -60.0)
CROP_FOV_DEG = 60.0
RING_CROP_COUNT = len(CROP_YAWS_DEG)
#: C6 looks straight down (x4-sphere-coverage PRD, milestone 1).
NADIR_CROP_INDEX = RING_CROP_COUNT
NADIR_FOV_DEG = 120.0
#: C6's forward is image-up, so its entry in the wire yaw table is 0.
NADIR_YAW_DEG = 0.0
#: Everything within this angle of the nadir is the wearer and is blacked out.
#: Provisional until milestone 2 measures it on the mount; 90 - 35 = 55 degrees
#: down keeps the PRD's "floor visible from 1 m" bar at a 1.4 m shoulder.
NADIR_BODY_MASK_DEG = 35.0
#: What ``InferenceMeta.yaws_deg`` carries: one entry per crop, C6 last.
CROPSET_YAWS_DEG: tuple[float, ...] = CROP_YAWS_DEG + (NADIR_YAW_DEG,)
#: Must equal ``cane.cloud.api_models.CROP_COUNT`` (test_dewarp pins it).
CROP_COUNT = len(CROPSET_YAWS_DEG)
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
    #: Pixels to black out after the remap (the wearer's body in C6), or None.
    body_mask: np.ndarray | None = None


@dataclass(frozen=True, eq=False)
class CropSet:
    """The crops of one equirect frame, in C0..C6 order (ring first, nadir last)."""

    images: tuple[np.ndarray, ...]
    yaws_deg: tuple[float, ...]
    t_arrival_laptop_ms: int

    @property
    def crop_px(self) -> int:
        return int(self.images[0].shape[0])


def focal_px(crop_px: int) -> float:
    """Pinhole focal length for a square crop of ``CROP_FOV_DEG`` horizontal FOV."""
    return (crop_px / 2.0) / math.tan(math.radians(CROP_FOV_DEG / 2.0))


def nadir_focal_px(crop_px: int) -> float:
    """Pinhole focal length of the ``NADIR_FOV_DEG`` nadir crop C6."""
    return (crop_px / 2.0) / math.tan(math.radians(NADIR_FOV_DEG / 2.0))


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


def _nadir_lut(width: int, height: int, crop_px: int) -> CropLut:
    """C6's (map_x, map_y, body_mask): a pinhole whose axis is the nadir."""
    focal = nadir_focal_px(crop_px)
    centre = crop_px / 2.0
    axis = np.arange(crop_px, dtype=np.float64) + 0.5 - centre
    x = axis[None, :]
    y = axis[:, None]
    shape = (crop_px, crop_px)
    # Image-up (-y) is forward, image-right (+x) is right, the axis (+focal)
    # points straight down -- the same world frame as ``_crop_lut``.
    world_x = np.broadcast_to(x, shape)
    world_z = np.broadcast_to(-y, shape)
    radius = np.sqrt(x**2 + y**2 + focal**2)
    lon = np.arctan2(world_x, world_z)  # 0 = forward, + = right
    lat = np.arcsin(-focal / radius)  # always below the horizon
    map_x = (lon / (2.0 * math.pi) + 0.5) * width
    map_y = (0.5 - lat / math.pi) * height
    body_mask = np.hypot(x, y) < focal * math.tan(math.radians(NADIR_BODY_MASK_DEG))
    return CropLut(
        yaw_deg=NADIR_YAW_DEG,
        map_x=np.ascontiguousarray(map_x, dtype=np.float32),
        map_y=np.ascontiguousarray(map_y, dtype=np.float32),
        body_mask=np.ascontiguousarray(body_mask),
    )


@lru_cache(maxsize=LUT_CACHE_SIZE)
def build_remap_luts(width: int, height: int, crop_px: int) -> tuple[CropLut, ...]:
    """All ``CROP_COUNT`` LUTs for one (frame size, crop size), ring first then
    C6. Cached: built once per run."""
    if crop_px <= 0:
        raise ValueError(f"crop_px must be > 0, got {crop_px}")
    ring = tuple(_crop_lut(width, height, crop_px, yaw) for yaw in CROP_YAWS_DEG)
    return ring + (_nadir_lut(width, height, crop_px),)


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


def _remap_crop(image: np.ndarray, lut: CropLut) -> np.ndarray:
    crop = cv2.remap(image, lut.map_x, lut.map_y, _REMAP_FLAGS, borderMode=_REMAP_BORDER)
    if lut.body_mask is not None:
        crop[lut.body_mask] = 0  # a fresh array from remap; nothing else holds it
    return crop


def dewarp_equirect(image: np.ndarray, crop_px: int, t_arrival_laptop_ms: int) -> CropSet:
    """``CROP_COUNT`` square ``crop_px`` crops: six ring crops covering the
    full 360 degrees between -30 and +30, and C6 covering every bearing from
    -30 down to the nadir."""
    width, height = _validate_equirect(image)
    luts = build_remap_luts(width, height, crop_px)
    crops = tuple(_remap_crop(image, lut) for lut in luts)
    return CropSet(
        images=crops, yaws_deg=CROPSET_YAWS_DEG, t_arrival_laptop_ms=t_arrival_laptop_ms
    )


def encode_jpeg(image: np.ndarray, quality: int) -> bytes:
    """Encode one crop. Raises rather than returning an empty payload."""
    ok, buffer = cv2.imencode(".jpg", image, (cv2.IMWRITE_JPEG_QUALITY, quality))
    if not ok:
        raise RuntimeError(f"cv2.imencode failed for a {image.shape} crop at q{quality}")
    return bytes(buffer)


def encode_cropset(cropset: CropSet, quality: int) -> tuple[bytes, ...]:
    return tuple(encode_jpeg(image, quality) for image in cropset.images)
