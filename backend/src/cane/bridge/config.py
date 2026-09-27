"""Immutable bridge configuration, validated at construction.

Every value the pipeline reads at runtime lives here, so a run is fully
described by one frozen object that can be logged verbatim.
"""

from __future__ import annotations

import logging
import re
import uuid
from pathlib import Path
from dataclasses import dataclass, field

from cane.bridge.rungs import RUNG_1, Rung
from cane.cloud.api_models import SESSION_ID_PATTERN

DEFAULT_WS_HOST = "0.0.0.0"
DEFAULT_WS_PORT = 8765
DEFAULT_INFERENCE_URL = "http://127.0.0.1:8000/infer"
# Step 8 E1: a request slower than this is a *timeout outcome*, not a slow
# RTT, so the health monitor and the uplink must agree. Defined here and
# imported by `backends.DEFAULT_BACKEND_TIMEOUT_S`, so there is one number
# and not two that happen to agree.
DEFAULT_REQUEST_TIMEOUT_S = 1.0
# The local detector's own budget. Larger than the cloud's on purpose: an
# MPS forward pass on six 640 px crops is tens of milliseconds warm, but a
# laptop that is thermally throttled or swapping can take far longer -- and
# an unbounded local call would hold the one-in-flight slot indefinitely,
# which is the exact failure the fallback exists to prevent.
DEFAULT_LOCAL_TIMEOUT_S = 2.0
DEFAULT_STATS_INTERVAL_S = 5.0
DEFAULT_SOURCE_SPEC = "synthetic"
# Plan Sec 3.3 ruling 1. The forward depth ring spans +-90 degrees in camera
# bearings, so a mount offset carries the emitted `gap_bearing_deg` out to
# +-(90 + |delta_deg|): bounding the offset at 30 caps that at 120 and keeps
# every bearing clear of the +-180 fold, where `normalize_bearing_deg` would
# quietly move a gap to the wearer's other side. Checked here, once, at
# startup -- a camera mounted crooked is a mounting error, and the ruling is
# explicit that it may never be paid for per frame (as first built, step 23
# dropped every steer frame above |delta_deg| = 1.875, which at the venue
# would have looked like steering simply never firing).
DELTA_DEG_MAX = 30.0
# Step 27 D, closing the judgement step 23 left open. The depth ring covers
# +-90 degrees in *camera* bearings, so an offset of N degrees leaves N degrees
# of the wearer's own forward arc unseen on one side (and looks N degrees past
# their shoulder on the other). At `DELTA_DEG_MAX` that is a third of the 90
# degrees on that side -- worth saying to whoever is straightening the camera,
# who can act on it. The hard failure stays at 30: this is a warning because the
# geometry is honest and the frame is still legal, not because it is harmless.
DELTA_DEG_WARN = 15.0
# The half-arc the depth ring covers in camera bearings, used only to state the
# warning's fraction. Imported nowhere: `gap_ring` owns the real geometry, and a
# second copy that could drift would be worse than a literal.
FORWARD_HALF_ARC_DEG = 90.0
ALLOWED_URL_SCHEMES = ("http://", "https://")
_SESSION_ID_RE = re.compile(SESSION_ID_PATTERN)
logger = logging.getLogger(__name__)


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
    local_timeout_s: float = DEFAULT_LOCAL_TIMEOUT_S
    send_fps: float | None = None
    ws_host: str = DEFAULT_WS_HOST
    ws_port: int = DEFAULT_WS_PORT
    allow_unsynced: bool = False
    stats_interval_s: float = DEFAULT_STATS_INTERVAL_S
    session_id: str = field(default_factory=_new_session_id)
    # Step 28: where to append the walk recording, or None for no recording.
    # Off by default, so the measured run and the unmeasured run are one run.
    record_events: Path | None = None

    def __post_init__(self) -> None:
        if not self.inference_url.startswith(ALLOWED_URL_SCHEMES):
            raise ValueError(
                f"inference_url must start with http:// or https://, got {self.inference_url!r}"
            )
        if self.request_timeout_s <= 0:
            raise ValueError(f"request_timeout_s must be > 0, got {self.request_timeout_s}")
        if self.local_timeout_s <= 0:
            raise ValueError(f"local_timeout_s must be > 0, got {self.local_timeout_s}")
        if abs(self.delta_deg) > DELTA_DEG_MAX:
            # Loudly, and here, so no steer frame ever pays for it
            # (plan Sec 3.3 ruling 1).
            raise ValueError(
                f"delta_deg must be within +-{DELTA_DEG_MAX:g} degrees of the wearer's "
                f"forward axis, got {self.delta_deg}: an offset that large is a mount "
                "or calibration fault, not a steering input (plan Sec 3.3 ruling 1)"
            )
        self._warn_about_the_blind_wedge()
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

    def _warn_about_the_blind_wedge(self) -> None:
        """Name what a large-but-legal mount offset costs (step 27 D).

        The offset is honest geometry and the frames it produces are legal, so
        this is not an error -- but 30 degrees of offset means 30 degrees of the
        wearer's forward arc is never looked at, and the person who can fix that
        is the one reading the log while the camera is still in their hand. A
        number they have to interpret is not the same as being told.
        """
        offset = abs(self.delta_deg)
        if offset <= DELTA_DEG_WARN:
            return
        blind_side = "left" if self.delta_deg > 0.0 else "right"
        seeing_side = "right" if self.delta_deg > 0.0 else "left"
        logger.warning(
            "delta_deg = %+.1f deg: %.1f degrees of your forward arc is unseen on the "
            "%s -- %.0f%% of that side -- because the depth ring covers only +-%.0f deg "
            "in camera bearings and the mount has rotated it. The same %.1f degrees "
            "behind your %s shoulder is covered instead. Legal (the limit is +-%.0f) "
            "but worth straightening.",
            self.delta_deg,
            offset,
            blind_side,
            100.0 * offset / FORWARD_HALF_ARC_DEG,
            FORWARD_HALF_ARC_DEG,
            offset,
            seeing_side,
            DELTA_DEG_MAX,
        )

    @property
    def effective_send_fps(self) -> float:
        """The rung's fps unless the CLI explicitly overrode it."""
        return self.send_fps if self.send_fps is not None else self.rung.fps

    @property
    def tick_period_s(self) -> float:
        return 1.0 / self.effective_send_fps
