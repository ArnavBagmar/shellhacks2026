"""The four uplink rungs from ``docs/camera-sync-design.md`` Sec 3.2.

| rung | crops | size | q | fps | uplink |
|---|---|---|---|---|---|
| 1 | 7 | 640x640 | 70 | 3 | ~7.6 Mbps |
| 2 | 7 | 640x640 | 70 | 2 | ~5.1 Mbps |
| 3 | 7 | 512x512 | 60 | 3 | ~2.7 Mbps |
| 4 | 7 | 512x512 | 60 | 2 | ~1.8 Mbps |

Sec 3.2 was written for six crops; the nadir crop C6 (x4-sphere-coverage)
adds a seventh and scales every uplink figure by 7/6. Its black body disc
compresses to almost nothing, so the real cost is below that.

Stepping between rungs is the health monitor's job (step 8); this module only
states what each rung *is*.
"""

from __future__ import annotations

from dataclasses import dataclass

from cane.cloud.api_models import CROP_COUNT

CROPS_PER_FRAME = CROP_COUNT

# Bytes per JPEG pixel, back-solved from the Mbps column of the table above so
# that `bitrate_estimate_mbps` reproduces the published numbers exactly. The
# doc marks those numbers un-measured (Sec 3.2 / Sec 6 spike list), so this is
# an estimate of an estimate -- never treat it as a measurement.
BYTES_PER_PIXEL_BY_QUALITY = {60: 0.06090, 70: 0.11020}
BITS_PER_BYTE = 8
BITS_PER_MEGABIT = 1_000_000


@dataclass(frozen=True)
class Rung:
    """One (crop size, JPEG quality, send rate) operating point."""

    index: int
    crop_px: int
    jpeg_quality: int
    fps: float
    crops: int = CROPS_PER_FRAME

    @property
    def period_s(self) -> float:
        return 1.0 / self.fps

    @property
    def bitrate_estimate_mbps(self) -> float:
        """Estimated uplink for this rung, matching the Sec 3.2 table."""
        bytes_per_pixel = BYTES_PER_PIXEL_BY_QUALITY[self.jpeg_quality]
        bytes_per_frame_set = self.crops * self.crop_px * self.crop_px * bytes_per_pixel
        return bytes_per_frame_set * BITS_PER_BYTE * self.fps / BITS_PER_MEGABIT


RUNG_1 = Rung(index=1, crop_px=640, jpeg_quality=70, fps=3.0)
RUNG_2 = Rung(index=2, crop_px=640, jpeg_quality=70, fps=2.0)
RUNG_3 = Rung(index=3, crop_px=512, jpeg_quality=60, fps=3.0)
RUNG_4 = Rung(index=4, crop_px=512, jpeg_quality=60, fps=2.0)
RUNGS: tuple[Rung, ...] = (RUNG_1, RUNG_2, RUNG_3, RUNG_4)


def rung_by_index(index: int) -> Rung:
    for rung in RUNGS:
        if rung.index == index:
            return rung
    raise ValueError(f"unknown rung {index}; choose one of {[r.index for r in RUNGS]}")
