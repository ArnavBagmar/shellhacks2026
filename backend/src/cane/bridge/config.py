"""Immutable bridge configuration, validated at construction.

Every value the pipeline reads at runtime lives here, so a run is fully
described by one frozen object that can be logged verbatim.
"""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass, field

from cane.bridge.rungs import RUNG_1, Rung
from cane.cloud.api_models import SESSION_ID_PATTERN

DEFAULT_WS_HOST = "0.0.0.0"
DEFAULT_WS_PORT = 8765
DEFAULT_INFERENCE_URL = "http://127.0.0.1:8000/infer"
DEFAULT_REQUEST_TIMEOUT_S = 2.0
DEFAULT_STATS_INTERVAL_S = 5.0
DEFAULT_SOURCE_SPEC = "synthetic"
MAX_DELTA_DEG = 360.0
ALLOWED_URL_SCHEMES = ("http://", "https://")
_SESSION_ID_RE = re.compile(SESSION_ID_PATTERN)


def _new_session_id() -> str:
    return f"bridge-{uuid.uuid4().hex[:12]}"


@dataclass(frozen=True)
class BridgeConfig:
    """One run of ``python -m cane.bridge``.

    ``delta_deg`` is the yaw calibration between "equirect centre column" and
    "the direction the wearer faces"; it absorbs any mounting offset so no
    other module ever hardcodes a different geometry.
    """

    inference_url: str = DEFAULT_INFERENCE_URL
    source_spec: str = DEFAULT_SOURCE_SPEC
    rung: Rung = RUNG_1
    delta_deg: float = 0.0
    pipeline_delay_d_ms: int = 0
    request_timeout_s: float = DEFAULT_REQUEST_TIMEOUT_S
    send_fps: float | None = None
    ws_host: str = DEFAULT_WS_HOST
    ws_port: int = DEFAULT_WS_PORT
    allow_unsynced: bool = False
    stats_interval_s: float = DEFAULT_STATS_INTERVAL_S
    session_id: str = field(default_factory=_new_session_id)

    def __post_init__(self) -> None:
        if not self.inference_url.startswith(ALLOWED_URL_SCHEMES):
            raise ValueError(
                f"inference_url must start with http:// or https://, got {self.inference_url!r}"
            )
        if self.request_timeout_s <= 0:
            raise ValueError(f"request_timeout_s must be > 0, got {self.request_timeout_s}")
        if abs(self.delta_deg) > MAX_DELTA_DEG:
            raise ValueError(f"delta_deg must be within +-360, got {self.delta_deg}")
        if self.send_fps is not None and self.send_fps <= 0:
            raise ValueError(f"send_fps must be > 0 when set, got {self.send_fps}")
        if _SESSION_ID_RE.fullmatch(self.session_id) is None:
            # Checked here so a bad --session-id fails at startup rather than
            # inside every InferenceMeta the pipeline builds.
            raise ValueError(
                f"session_id must match {SESSION_ID_PATTERN}, got {self.session_id!r}"
            )
        if self.stats_interval_s <= 0:
            raise ValueError(f"stats_interval_s must be > 0, got {self.stats_interval_s}")

    @property
    def effective_send_fps(self) -> float:
        """The rung's fps unless the CLI explicitly overrode it."""
        return self.send_fps if self.send_fps is not None else self.rung.fps

    @property
    def tick_period_s(self) -> float:
        return 1.0 / self.effective_send_fps
