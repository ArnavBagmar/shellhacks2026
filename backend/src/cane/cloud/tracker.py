"""ByteTrack per (session, crop), generation-stamped ids, bounded state (A7).

The only module in the cloud package that imports `supervision`. Everything
above it sees :class:`TrackedBox`, so swapping ByteTrack for the `trackers`
package -- or for the greedy-IoU escape hatch the architect sketched -- is a
one-file change.

Two timers that are easy to conflate. `LOST_TRACK_BUFFER` is ByteTrack's own,
in *frames*: supervision computes `max_time_lost = int(frame_rate / 30 *
lost_track_buffer)`, so it is a duration knob only when `frame_rate` is the
real rung fps -- left at the defaults, a 3 fps rung keeps zombies for 10 s.
`TRACK_IDLE_EVICT_MS` retires a whole `(session, crop)` entry and is measured
in echoed `t_capture_ms` deltas, never a clock (A9).
"""

from __future__ import annotations

import itertools
import logging
import warnings
from dataclasses import dataclass, field
from typing import Final, Mapping

import numpy as np
import supervision as sv
from numpy.typing import NDArray

from cane.cloud.motion import MotionState

logger = logging.getLogger(__name__)

# docs/camera-sync-design.md Sec 3.2. A rung we do not know falls back to the
# most common rate rather than to supervision's 30, which would be a 10x error.
RUNG_FPS: Final[Mapping[int, int]] = {1: 3, 2: 2, 3: 3, 4: 2}
DEFAULT_FPS: Final = 3
SUPERVISION_REFERENCE_FPS: Final = 30
LOST_TRACK_S: Final = 2.0
LOST_TRACK_BUFFER: Final = int(LOST_TRACK_S * SUPERVISION_REFERENCE_FPS)
# cost = 1 - IoU, so 0.9 accepts IoU > 0.1. Loosened from supervision's 0.8:
# at 2-3 fps a crossing cyclist's boxes barely overlap frame to frame.
MATCH_THRESHOLD: Final = 0.9
MIN_CONSECUTIVE_FRAMES: Final = 1
DEFAULT_ACTIVATION_THRESHOLD: Final = 0.35
# Emergent, not chosen: supervision's *new track* floor is
# `track_activation_threshold + NEW_TRACK_SCORE_MARGIN`, so with A8's
# activation = conf_emit = 0.35 a box that never rises above 0.45 can join an
# existing track but never start one. Pinned by a test, raised as an open
# concern rather than "fixed" here, because A8 fixes the activation value.
NEW_TRACK_SCORE_MARGIN: Final = 0.1

TRACK_IDLE_EVICT_MS: Final = 5000
DEFAULT_MAX_SESSIONS: Final = 8
DET_INDEX_KEY: Final = "det_index"

# supervision 0.30.5 deprecated `ByteTrack` (removal in 0.31.0) and warns from
# its constructor. Silenced at that one call site rather than by a pytest ini
# filter, which would also hide the next deprecation. Construction is
# serialised by the app's inference lock, so the filter swap is not racy.
_DEPRECATION_MESSAGE: Final = r".*`ByteTrack` was deprecated.*"

_GENERATION = itertools.count(1)


def next_generation() -> int:
    """Process-wide, monotonic. ByteTrack restarts its ids at 1 whenever a
    tracker is rebuilt, so a bare `crop:id` can name a different physical
    object minutes later -- and the phone's 8 s urgency-1 refractory would
    then silently suppress the new one.
    """
    return next(_GENERATION)


@dataclass(frozen=True, slots=True)
class TrackedBox:
    """A surviving detection: where it was in the caller's input, and its id."""

    det_index: int
    track_id: str


class CropTracker:
    """One ByteTrack instance for one crop of one session."""

    def __init__(
        self,
        fps: float,
        crop_index: int,
        gen: int,
        activation_threshold: float = DEFAULT_ACTIVATION_THRESHOLD,
        match_threshold: float = MATCH_THRESHOLD,
    ) -> None:
        self._crop_index = crop_index
        self._gen = gen
        self._prefix = f"{crop_index}:{gen}:"
        with warnings.catch_warnings():
            warnings.filterwarnings("ignore", message=_DEPRECATION_MESSAGE)
            self._tracker = sv.ByteTrack(
                track_activation_threshold=activation_threshold,
                lost_track_buffer=LOST_TRACK_BUFFER,
                minimum_matching_threshold=match_threshold,
                frame_rate=fps,
                minimum_consecutive_frames=MIN_CONSECUTIVE_FRAMES,
            )

    def update(
        self,
        xyxy: NDArray[np.float32],
        confidence: NDArray[np.float32],
        class_ids: NDArray[np.int32],
    ) -> tuple[TrackedBox, ...]:
        """Advance one frame. Call this for *every* frame, including empty
        ones: skipping an empty crop freezes the internal frame counter and
        lost tracks never age out.

        `update_with_detections` returns a filtered subset, so the caller's
        index travels in `data` rather than being recovered by position.
        """
        count = int(xyxy.shape[0])
        detections = sv.Detections(
            xyxy=xyxy.reshape(count, 4).astype(np.float32, copy=False),
            confidence=confidence.astype(np.float32, copy=False),
            class_id=class_ids.astype(int, copy=False),
            data={DET_INDEX_KEY: np.arange(count, dtype=int)},
        )
        tracked = self._tracker.update_with_detections(detections)
        # An empty result carries neither `tracker_id` nor our `data` key:
        # supervision rebuilds `data` from the surviving rows, and zero rows
        # means zero keys. Read either only after this guard.
        if len(tracked) == 0 or tracked.tracker_id is None:
            return ()
        indices = tracked.data[DET_INDEX_KEY]
        return tuple(
            TrackedBox(det_index=int(index), track_id=f"{self._prefix}{int(track)}")
            for index, track in zip(indices, tracked.tracker_id, strict=True)
        )


@dataclass
class SessionCrop:
    """Mutable per-(session, crop) state: the tracker, each track's range
    history, and the last capture stamp that touched it.
    """

    tracker: CropTracker
    rung: int
    last_t_capture_ms: int
    motion: dict[str, MotionState] = field(default_factory=dict)
    rung_change_logged: bool = False


class TrackerRegistry:
    """Every live `(session_id, crop_index)` entry, bounded in both directions.

    `session_id` is client-supplied, so an unbounded dict keyed on it is a
    memory leak on an internet-facing VM: sessions are capped and the least
    recently used one is dropped whole.
    """

    def __init__(
        self,
        max_sessions: int = DEFAULT_MAX_SESSIONS,
        idle_evict_ms: int = TRACK_IDLE_EVICT_MS,
        activation_threshold: float = DEFAULT_ACTIVATION_THRESHOLD,
    ) -> None:
        if max_sessions < 1:
            raise ValueError(f"max_sessions must be >= 1, got {max_sessions}")
        self._max_sessions = max_sessions
        self._idle_evict_ms = idle_evict_ms
        self._activation_threshold = activation_threshold
        self._entries: dict[tuple[str, int], SessionCrop] = {}

    @property
    def session_count(self) -> int:
        return len(self.sessions)

    @property
    def sessions(self) -> tuple[str, ...]:
        """Distinct session ids, oldest activity first."""
        newest: dict[str, int] = {}
        for (session_id, _), entry in self._entries.items():
            newest[session_id] = max(newest.get(session_id, 0), entry.last_t_capture_ms)
        return tuple(sorted(newest, key=lambda s: (newest[s], s)))

    def entry_for(
        self, session_id: str, crop_index: int, rung: int, t_capture_ms: int
    ) -> SessionCrop:
        key = (session_id, crop_index)
        entry = self._entries.get(key)
        if entry is None:
            entry = self._build(crop_index, rung, t_capture_ms)
            self._entries[key] = entry
            self._enforce_session_cap(session_id)
        elif entry.rung != rung and not entry.rung_change_logged:
            # Rebuilding the tracker would churn every id on this crop.
            logger.info(
                "session %s crop %d stepped rung %d -> %d; keeping frame_rate=%d",
                session_id, crop_index, entry.rung, rung, RUNG_FPS.get(entry.rung, DEFAULT_FPS),
            )
            entry.rung_change_logged = True
        entry.last_t_capture_ms = t_capture_ms
        return entry

    def sweep(self, t_capture_ms: int) -> int:
        """Retire idle entries and stale range histories. Returns entries removed."""
        stale = [
            key
            for key, entry in self._entries.items()
            if t_capture_ms - entry.last_t_capture_ms > self._idle_evict_ms
        ]
        for key in stale:
            del self._entries[key]
        for entry in self._entries.values():
            self._prune_motion(entry, t_capture_ms)
        return len(stale)

    def _build(self, crop_index: int, rung: int, t_capture_ms: int) -> SessionCrop:
        tracker = CropTracker(
            fps=float(RUNG_FPS.get(rung, DEFAULT_FPS)),
            crop_index=crop_index,
            gen=next_generation(),
            activation_threshold=self._activation_threshold,
        )
        return SessionCrop(tracker=tracker, rung=rung, last_t_capture_ms=t_capture_ms)

    def _prune_motion(self, entry: SessionCrop, t_capture_ms: int) -> None:
        dead = [
            track_id
            for track_id, state in entry.motion.items()
            if t_capture_ms - state.last_t_capture_ms > self._idle_evict_ms
        ]
        for track_id in dead:
            del entry.motion[track_id]

    def _enforce_session_cap(self, keep: str) -> None:
        while self.session_count > self._max_sessions:
            oldest = next(s for s in self.sessions if s != keep)
            for key in [k for k in self._entries if k[0] == oldest]:
                del self._entries[key]
            logger.info("evicted LRU session %s (cap %d)", oldest, self._max_sessions)

