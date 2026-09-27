"""Taller, downward-tilted perspective views of the 360 frame, for Gemini.

The bridge's six crops are 60 x 60 degree squares centred on the horizon
(right for the hazard detector). With the X4 worn on the head they see only
~30 degrees below eye level, so a table in front of the wearer is out of
frame (2026-09-26: "it can only see stuff at head level"). These views keep
the same six headings and 60-degree width, but are ``vfov_deg`` tall and
tilted ``pitch_deg`` (negative = down): with -20 and 100 they cover from 30
degrees above eye level to 70 below.

Pixel <-> direction is exact pinhole geometry, both ways, so a position
Gemini reports in a tilted view turns into the right bearing.
"""

from __future__ import annotations

import math
from functools import lru_cache

import cv2
import numpy as np

YAWS_DEG: tuple[float, ...] = (0.0, 60.0, 120.0, 180.0, -120.0, -60.0)
HFOV_DEG = 60.0
#: The head-worn X4: 30 degrees above eye level to 70 below.
HEAD_PITCH_DEG = -20.0
HEAD_VFOV_DEG = 100.0


def height_for(width: int, hfov_deg: float, vfov_deg: float) -> int:
    """Pixel height giving square pixels for a ``hfov`` x ``vfov`` view."""
    return max(1, round(width * math.tan(math.radians(vfov_deg) / 2) / math.tan(math.radians(hfov_deg) / 2)))


def bearing_offset(x: float, y: float, *, hfov_deg: float, vfov_deg: float, pitch_deg: float) -> float:
    """Horizontal angle (degrees, + = right) from the view's heading to the
    direction through image point (x, y), both 0..1 from the top-left.
    """
    cam_x = (x - 0.5) * 2 * math.tan(math.radians(hfov_deg) / 2)
    cam_y = (y - 0.5) * 2 * math.tan(math.radians(vfov_deg) / 2)  # + = down
    down = math.radians(-pitch_deg)
    world_z = -cam_y * math.sin(down) + math.cos(down)
    return math.degrees(math.atan2(cam_x, world_z))


def x_for_offset(offset_deg: float, *, hfov_deg: float, pitch_deg: float) -> float | None:
    """Where on the horizon row a heading ``offset_deg`` from the view's
    centre falls (0..1), or ``None`` if the view cannot see it.
    """
    a = math.radians(offset_deg)
    if math.cos(a) <= 0:
        return None
    cam_x = math.tan(a) / math.cos(math.radians(-pitch_deg))
    x = 0.5 + cam_x / (2 * math.tan(math.radians(hfov_deg) / 2))
    return x


@lru_cache(maxsize=8)
def _luts(
    width: int, height: int, crop_w: int, crop_h: int, hfov_deg: float, vfov_deg: float, pitch_deg: float
) -> tuple[tuple[np.ndarray, np.ndarray], ...]:
    tan_h = math.tan(math.radians(hfov_deg) / 2)
    tan_v = math.tan(math.radians(vfov_deg) / 2)
    cam_x = ((np.arange(crop_w) + 0.5) / crop_w - 0.5) * 2 * tan_h
    cam_y = ((np.arange(crop_h) + 0.5) / crop_h - 0.5) * 2 * tan_v
    cx, cy = np.meshgrid(cam_x, cam_y)
    down = math.radians(-pitch_deg)
    # Tilt: the camera's forward axis points `down` below the horizon (y is down).
    wx = cx
    wy = cy * math.cos(down) + math.sin(down)
    wz = -cy * math.sin(down) + math.cos(down)
    out = []
    for yaw_deg in YAWS_DEG:
        yaw = math.radians(yaw_deg)
        rx = wx * math.cos(yaw) + wz * math.sin(yaw)
        rz = -wx * math.sin(yaw) + wz * math.cos(yaw)
        radius = np.sqrt(rx**2 + wy**2 + rz**2)
        lon = np.arctan2(rx, rz)
        lat = np.arcsin(-wy / radius)
        map_x = ((lon / (2 * math.pi) + 0.5) * width).astype(np.float32)
        map_y = ((0.5 - lat / math.pi) * height).astype(np.float32)
        out.append((map_x, map_y))
    return tuple(out)


def tall_views(
    equirect: np.ndarray, width_px: int, *, vfov_deg: float, pitch_deg: float, hfov_deg: float = HFOV_DEG
) -> list[np.ndarray]:
    """Six views in ``YAWS_DEG`` order, each ``width_px`` wide."""
    if equirect.ndim != 3 or equirect.shape[1] != 2 * equirect.shape[0]:
        raise ValueError(f"tall views need a 2:1 equirect frame, got shape {equirect.shape}")
    height, width = int(equirect.shape[0]), int(equirect.shape[1])
    crop_h = height_for(width_px, hfov_deg, vfov_deg)
    luts = _luts(width, height, width_px, crop_h, hfov_deg, vfov_deg, pitch_deg)
    return [cv2.remap(equirect, mx, my, cv2.INTER_LINEAR, borderMode=cv2.BORDER_WRAP) for mx, my in luts]
