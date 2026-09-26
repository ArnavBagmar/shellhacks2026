"""Runtime configuration for the inference service (step-07-design A3).

The container entrypoint and the laptop run use the *same* names -- there is
no `ENGINE_PATH` / `CANE_ENGINE_PATH` split to get wrong at 3 a.m.

No field may start with `model_`: pydantic reserves that namespace and emits
a `UserWarning` when a field collides with it, which is a hard failure under
the suite's `-W error`. Hence `weights_path`.
"""

from __future__ import annotations

from pathlib import Path
from typing import Final, Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

DetectorName = Literal["fake", "ultralytics", "tensorrt"]

DEFAULT_WEIGHTS_PATH: Final = Path("yolo11s.pt")
DEFAULT_ENGINE_PATH: Final = Path("/models/yolo11s_b12dyn_fp16.engine")
DEFAULT_CONF_DETECT: Final = 0.15
DEFAULT_CONF_EMIT: Final = 0.35
DEFAULT_IOU: Final = 0.45
DEFAULT_MAX_DET_PER_CROP: Final = 20
DEFAULT_MAX_CROP_BYTES: Final = 2_000_000
DEFAULT_MAX_SESSIONS: Final = 8
DEFAULT_PORT: Final = 8000
DEFAULT_DUMP_EVERY_N: Final = 30


class CloudSettings(BaseSettings):
    """Every knob, read once from `CANE_*` and then immutable."""

    model_config = SettingsConfigDict(env_prefix="CANE_", frozen=True, extra="ignore")

    detector: DetectorName = "fake"
    weights_path: Path = DEFAULT_WEIGHTS_PATH
    engine_path: Path = DEFAULT_ENGINE_PATH
    device: str = "cpu"
    # Fed to YOLO. Lower than `conf_emit` on purpose: the BYTE half of
    # ByteTrack uses 0.15-0.35 boxes to keep an existing track alive through a
    # blurry frame, which is exactly where an overtaking cyclist's id churns.
    conf_detect: float = Field(default=DEFAULT_CONF_DETECT, ge=0.0, le=1.0)
    # The contract threshold: nothing below this reaches the wire.
    conf_emit: float = Field(default=DEFAULT_CONF_EMIT, ge=0.0, le=1.0)
    iou: float = Field(default=DEFAULT_IOU, ge=0.0, le=1.0)
    max_det_per_crop: int = Field(default=DEFAULT_MAX_DET_PER_CROP, ge=1, le=300)
    max_crop_bytes: int = Field(default=DEFAULT_MAX_CROP_BYTES, ge=1024)
    max_sessions: int = Field(default=DEFAULT_MAX_SESSIONS, ge=1, le=128)
    api_key: str = ""  # empty = open, for local dev
    port: int = Field(default=DEFAULT_PORT, ge=1, le=65535)
    log_level: str = "INFO"
    # Debug only: annotated contact sheets for the live acceptance run. Off
    # unless CANE_DUMP_DIR is set, and never inside the measured request path.
    dump_dir: Path | None = None
    dump_every_n: int = Field(default=DEFAULT_DUMP_EVERY_N, ge=1)
