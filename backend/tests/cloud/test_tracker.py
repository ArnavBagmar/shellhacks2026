"""ByteTrack per (session, crop), generation-stamped ids, bounded state (A7).

Everything here is behavioural. `supervision.ByteTrack` is deprecated in
0.30.5 and scheduled for removal in 0.31.0, so asserting on its internals
(`max_time_lost`) would pin us to one release; asserting on what a track
*does* survives the swap to the `trackers` package or to the greedy-IoU
escape hatch.
"""

from __future__ import annotations

import re

import numpy as np
import pytest

from cane.cloud.tracker import (
    DEFAULT_ACTIVATION_THRESHOLD,
    LOST_TRACK_BUFFER,
    MATCH_THRESHOLD,
    RUNG_FPS,
    TRACK_IDLE_EVICT_MS,
    CropTracker,
    TrackerRegistry,
    next_generation,
)

T0 = 1_790_000_000_000
BOX_W = 80.0
BOX_H = 200.0
CONF = 0.8
# 3 fps and a box crossing 60 px per frame: the case the default matching
# threshold of 0.8 drops (see the test below) and 0.9 keeps.
FAST_STEP_PX = 60.0
SLOW_STEP_PX = 2.0
FRAMES = 30
TRACK_ID_RE = re.compile(r"^(\d+):(\d+):(\d+)$")


def _boxes(*x_lefts: float) -> np.ndarray:
    return np.array(
        [[x, 100.0, x + BOX_W, 100.0 + BOX_H] for x in x_lefts], dtype=np.float32
    )


def _conf(n: int, value: float = CONF) -> np.ndarray:
    return np.full(n, value, dtype=np.float32)


def _classes(n: int) -> np.ndarray:
    return np.zeros(n, dtype=np.int32)


def _empty() -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    return np.zeros((0, 4), dtype=np.float32), _conf(0), _classes(0)


def _new_tracker(fps: float = 3.0, crop_index: int = 0, **kwargs: float) -> CropTracker:
    return CropTracker(fps=fps, crop_index=crop_index, gen=next_generation(), **kwargs)


def _drive(tracker: CropTracker, step_px: float, frames: int = FRAMES) -> list[str | None]:
    ids: list[str | None] = []
    for i in range(frames):
        tracked = tracker.update(_boxes(20.0 + step_px * i), _conf(1), _classes(1))
        ids.append(tracked[0].track_id if tracked else None)
    return ids


def test_supervision_constructor_accepts_our_kwargs() -> None:
    """A renamed kwarg in a supervision bump is a TypeError right here."""
    for fps in sorted(set(RUNG_FPS.values())):
        tracker = _new_tracker(fps=float(fps))
        tracked = tracker.update(_boxes(20.0), _conf(1), _classes(1))
        assert len(tracked) == 1
    assert MATCH_THRESHOLD == 0.9
    assert LOST_TRACK_BUFFER == 60


def test_track_id_stable_across_30_frames_slow_box() -> None:
    ids = _drive(_new_tracker(), SLOW_STEP_PX)
    assert len(ids) == FRAMES
    assert len(set(ids)) == 1 and ids[0] is not None


def test_track_id_stable_across_30_frames_fast_box() -> None:
    ids = _drive(_new_tracker(), FAST_STEP_PX)
    assert len(ids) == FRAMES
    assert len(set(ids)) == 1 and ids[0] is not None


def test_the_default_match_threshold_would_lose_the_fast_box() -> None:
    """Why MATCH_THRESHOLD is loosened from supervision's 0.8 (A7).

    ByteTrack's Kalman filter assumes ~30 fps; at 3 fps a crossing cyclist's
    boxes barely overlap and the default rejects every match, so the track
    silently stops being reported -- announces nothing, no error.
    """
    ids = _drive(_new_tracker(match_threshold=0.8), FAST_STEP_PX)
    assert ids.count(None) == FRAMES - 1


def test_a_box_at_0_40_starts_a_track_on_its_first_frame() -> None:
    """R-A8. supervision derives its new-track floor as
    `track_activation_threshold + 0.1`, so the tracker must be constructed
    0.1 *below* the emit threshold. Built at the emit threshold instead, the
    first frame of a 0.40 cyclist starts no track and is never announced.
    """
    tracker = _new_tracker()
    tracked = tracker.update(_boxes(20.0), _conf(1, 0.40), _classes(1))
    assert len(tracked) == 1


def test_a_box_at_0_30_never_starts_a_track() -> None:
    """The floor still bites below the emit threshold: 0.30 is association
    fodder only, so nothing under `conf_emit` can appear out of nowhere.
    """
    tracker = _new_tracker()
    for step in range(3):
        assert tracker.update(_boxes(20.0 + 2.0 * step), _conf(1, 0.30), _classes(1)) == ()


def test_a_box_at_0_20_keeps_an_existing_track_alive() -> None:
    """The BYTE half: a 0.20 box cannot start a track, but it is matched to a
    live one *on that frame* -- proved by the id coming back, not by the track
    merely surviving in the lost buffer.
    """
    tracker = _new_tracker()
    first = tracker.update(_boxes(20.0), _conf(1), _classes(1))[0].track_id
    low = tracker.update(_boxes(22.0), _conf(1, 0.20), _classes(1))
    assert [box.track_id for box in low] == [first]


def test_two_crops_have_independent_ids() -> None:
    left, right = _new_tracker(crop_index=0), _new_tracker(crop_index=3)
    left_id = left.update(_boxes(20.0), _conf(1), _classes(1))[0].track_id
    right_id = right.update(_boxes(20.0), _conf(1), _classes(1))[0].track_id
    assert left_id != right_id
    assert left_id.startswith("0:") and right_id.startswith("3:")


def test_det_index_mapping_survives_tracking() -> None:
    """`update_with_detections` returns a *filtered subset*; zipping it
    positionally against the input silently mislabels every detection.
    """
    tracker = _new_tracker()
    conf = np.array([CONF, DEFAULT_ACTIVATION_THRESHOLD - 0.15, CONF], dtype=np.float32)
    tracked = tracker.update(_boxes(20.0, 300.0, 500.0), conf, _classes(3))
    assert [box.det_index for box in tracked] == [0, 2]


def test_empty_frame_still_ages_lost_tracks() -> None:
    """A crop with zero detections must still drive the tracker; skipping it
    freezes the frame counter and lost tracks never age out.
    """
    tracker = _new_tracker()
    first = tracker.update(_boxes(20.0), _conf(1), _classes(1))[0].track_id
    for _ in range(8):
        assert tracker.update(*_empty()) == ()
    returned = tracker.update(_boxes(24.0), _conf(1), _classes(1))
    assert all(box.track_id != first for box in returned)


def test_track_survives_two_seconds_of_misses_and_then_dies() -> None:
    """LOST_TRACK_S = 2.0 s: 6 missed frames at rung 1's 3 fps."""
    survivor = _new_tracker()
    first = survivor.update(_boxes(20.0), _conf(1), _classes(1))[0].track_id
    for _ in range(6):
        survivor.update(*_empty())
    assert survivor.update(_boxes(24.0), _conf(1), _classes(1))[0].track_id == first

    doomed = _new_tracker()
    gone = doomed.update(_boxes(20.0), _conf(1), _classes(1))[0].track_id
    for _ in range(8):
        doomed.update(*_empty())
    assert all(b.track_id != gone for b in doomed.update(_boxes(24.0), _conf(1), _classes(1)))


def test_track_id_encodes_crop_index_and_generation() -> None:
    tracker = CropTracker(fps=3.0, crop_index=4, gen=77)
    track_id = tracker.update(_boxes(20.0), _conf(1), _classes(1))[0].track_id
    match = TRACK_ID_RE.match(track_id)
    assert match is not None
    assert (int(match.group(1)), int(match.group(2))) == (4, 77)
    assert len(track_id) <= 64


def test_generation_increments_so_ids_never_repeat() -> None:
    """ByteTrack restarts ids at 1 after eviction, so `2:7` could name a
    different physical object 5 s later -- and the phone's 8 s urgency-1
    refractory would silently swallow it.
    """
    first = _new_tracker(crop_index=2).update(_boxes(20.0), _conf(1), _classes(1))[0]
    second = _new_tracker(crop_index=2).update(_boxes(20.0), _conf(1), _classes(1))[0]
    assert first.track_id != second.track_id
    assert next_generation() != next_generation()


def test_registry_evicts_idle_entry_after_5s_of_capture_time() -> None:
    """Measured in echoed capture stamps -- there is no clock in this path."""
    registry = TrackerRegistry()
    registry.entry_for("s1", 0, rung=1, t_capture_ms=T0)
    assert registry.session_count == 1
    assert registry.sweep(T0 + TRACK_IDLE_EVICT_MS) == 0
    assert registry.session_count == 1
    assert registry.sweep(T0 + TRACK_IDLE_EVICT_MS + 1) == 1
    assert registry.session_count == 0


def test_registry_caps_sessions() -> None:
    """An unbounded dict keyed on a client-supplied string is a memory leak."""
    registry = TrackerRegistry(max_sessions=2)
    for index, session in enumerate(("a", "b", "c")):
        registry.entry_for(session, 0, rung=1, t_capture_ms=T0 + index)
    assert registry.session_count == 2
    assert registry.sessions == ("b", "c")


def test_registry_keeps_every_crop_of_a_session() -> None:
    registry = TrackerRegistry(max_sessions=1)
    entries = [registry.entry_for("a", crop, rung=1, t_capture_ms=T0) for crop in range(6)]
    assert registry.session_count == 1
    assert len({id(entry) for entry in entries}) == 6


def test_rung_change_does_not_rebuild_the_tracker() -> None:
    """Rebuilding would churn every id on the crop; the only cost of keeping
    the old frame_rate is ~1 s of lost-track tolerance. Id stability wins.
    """
    registry = TrackerRegistry()
    first = registry.entry_for("s1", 0, rung=1, t_capture_ms=T0)
    first_id = first.tracker.update(_boxes(20.0), _conf(1), _classes(1))[0].track_id
    second = registry.entry_for("s1", 0, rung=3, t_capture_ms=T0 + 333)
    assert second is first
    assert second.tracker is first.tracker
    assert second.tracker.update(_boxes(22.0), _conf(1), _classes(1))[0].track_id == first_id


def test_registry_prunes_motion_state_in_the_same_sweep() -> None:
    from cane.cloud.motion import MotionState

    registry = TrackerRegistry()
    entry = registry.entry_for("s1", 0, rung=1, t_capture_ms=T0)
    entry.motion["0:1:1"] = MotionState(10.0, T0, 1.7, None)
    entry.motion["0:1:2"] = MotionState(10.0, T0 + 4000, 1.7, None)
    registry.entry_for("s1", 0, rung=1, t_capture_ms=T0 + 5200)
    registry.sweep(T0 + 5200)
    assert set(entry.motion) == {"0:1:2"}


def test_tracked_box_is_frozen() -> None:
    box = _new_tracker().update(_boxes(20.0), _conf(1), _classes(1))[0]
    with pytest.raises((AttributeError, TypeError)):
        box.det_index = 9  # type: ignore[misc]
