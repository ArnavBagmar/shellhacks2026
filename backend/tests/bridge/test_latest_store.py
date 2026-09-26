"""The single-slot newest-frame store (F2 rule 1): never a queue.

A queue would hand the consumer stale frames; every test here is about the
store throwing intermediate frames away instead of keeping them.
"""

from __future__ import annotations

import threading
import time

import numpy as np

from cane.bridge.capture.latest import LatestFrameStore

FRAME_H = 4
FRAME_W = 8
WAIT_S = 1.0
SHORT_WAIT_S = 0.05
BASE_MS = 1_790_000_000_000


def _image(value: int) -> np.ndarray:
    return np.full((FRAME_H, FRAME_W, 3), value, dtype=np.uint8)


def test_newest_wins_ten_pushes_one_wait_returns_the_tenth() -> None:
    store = LatestFrameStore()
    for i in range(1, 11):
        store.push(_image(i), BASE_MS + i)

    frame = store.wait_for_new(last_seq=0, timeout_s=WAIT_S)

    assert frame is not None
    assert int(frame.image[0, 0, 0]) == 10
    assert frame.t_arrival_laptop_ms == BASE_MS + 10
    assert frame.seq == 10


def test_intermediate_frames_are_discarded_not_queued() -> None:
    store = LatestFrameStore()
    store.push(_image(1), BASE_MS + 1)
    first = store.wait_for_new(last_seq=0, timeout_s=WAIT_S)
    assert first is not None

    for i in range(2, 7):
        store.push(_image(i), BASE_MS + i)
    second = store.wait_for_new(last_seq=first.seq, timeout_s=WAIT_S)

    assert second is not None
    assert int(second.image[0, 0, 0]) == 6
    assert second.seq == 6


def test_sequence_numbers_are_monotonic_across_waits() -> None:
    store = LatestFrameStore()
    last_seq = 0
    seen: list[int] = []
    for i in range(5):
        store.push(_image(i), BASE_MS + i)
        frame = store.wait_for_new(last_seq=last_seq, timeout_s=WAIT_S)
        assert frame is not None
        seen.append(frame.seq)
        last_seq = frame.seq

    assert seen == sorted(seen)
    assert len(set(seen)) == len(seen)


def test_wait_after_fail_returns_none() -> None:
    store = LatestFrameStore()
    store.push(_image(1), BASE_MS)
    store.fail()

    assert store.wait_for_new(last_seq=0, timeout_s=WAIT_S) is None


def test_fail_wakes_a_blocked_waiter() -> None:
    store = LatestFrameStore()
    result: list[object] = []

    def waiter() -> None:
        result.append(store.wait_for_new(last_seq=0, timeout_s=WAIT_S))

    thread = threading.Thread(target=waiter)
    thread.start()
    time.sleep(SHORT_WAIT_S)
    store.fail()
    thread.join(timeout=WAIT_S)

    assert not thread.is_alive()
    assert result == [None]


def test_wait_times_out_when_no_new_frame_arrives() -> None:
    store = LatestFrameStore()
    store.push(_image(1), BASE_MS)
    frame = store.wait_for_new(last_seq=0, timeout_s=WAIT_S)
    assert frame is not None

    started = time.monotonic()
    assert store.wait_for_new(last_seq=frame.seq, timeout_s=SHORT_WAIT_S) is None
    assert time.monotonic() - started >= SHORT_WAIT_S


def test_pushed_count_tracks_every_push_even_the_dropped_ones() -> None:
    store = LatestFrameStore()
    for i in range(7):
        store.push(_image(i), BASE_MS + i)

    assert store.pushed_count == 7


def test_frames_are_immutable_value_objects() -> None:
    store = LatestFrameStore()
    store.push(_image(3), BASE_MS)
    frame = store.wait_for_new(last_seq=0, timeout_s=WAIT_S)
    assert frame is not None

    try:
        frame.seq = 99  # type: ignore[misc]
    except Exception as exc:  # dataclasses.FrozenInstanceError
        assert type(exc).__name__ == "FrozenInstanceError"
    else:  # pragma: no cover - only reached if Frame is mutable
        raise AssertionError("Frame must be frozen")
