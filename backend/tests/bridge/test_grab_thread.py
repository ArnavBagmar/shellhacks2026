"""The dedicated grab thread (F2 rule 1): a slow consumer still gets fresh frames.

The acceptance shape here mirrors the real pipeline: the source produces at
30 fps, the consumer works for 200 ms per tick, and every frame it picks up
must still be younger than 40 ms.
"""

from __future__ import annotations

import time

import pytest

from cane.bridge.capture.grab_thread import GrabThread
from cane.bridge.capture.synthetic_source import SyntheticEquirectSource

SOURCE_FPS = 30.0
SMALL_W = 160
SMALL_H = 80
CONSUMER_WORK_S = 0.2
PICKUPS = 8
MAX_AGE_AT_PICKUP_MS = 40
WAIT_S = 2.0


@pytest.fixture()
def source() -> SyntheticEquirectSource:
    src = SyntheticEquirectSource(width=SMALL_W, height=SMALL_H, fps=SOURCE_FPS)
    yield src
    src.close()


def test_slow_consumer_always_gets_a_frame_younger_than_40ms(
    source: SyntheticEquirectSource,
) -> None:
    ages: list[int] = []
    with GrabThread(source) as grab:
        last_seq = 0
        for _ in range(PICKUPS):
            time.sleep(CONSUMER_WORK_S)
            frame = grab.wait_for_new(last_seq, timeout_s=WAIT_S)
            assert frame is not None
            ages.append(int(time.time() * 1000) - frame.t_arrival_laptop_ms)
            last_seq = frame.seq

    assert len(ages) == PICKUPS
    assert max(ages) < MAX_AGE_AT_PICKUP_MS, f"stale pickup: ages={ages}"


def test_grab_thread_reads_continuously_not_on_demand(
    source: SyntheticEquirectSource,
) -> None:
    with GrabThread(source) as grab:
        time.sleep(CONSUMER_WORK_S * 2)
        grabbed = grab.frames_grabbed

    # ~30 fps for 0.4 s is ~12 frames; a lazily-read source would show ~0.
    assert grabbed >= 6


def test_stop_is_idempotent_and_joins_the_thread(
    source: SyntheticEquirectSource,
) -> None:
    grab = GrabThread(source)
    grab.start()
    frame = grab.wait_for_new(0, timeout_s=WAIT_S)
    assert frame is not None

    assert grab.stop() is True
    assert grab.stop() is True
    assert not grab.is_alive


def test_wait_returns_none_once_the_source_is_exhausted() -> None:
    src = SyntheticEquirectSource(width=SMALL_W, height=SMALL_H, fps=200.0, max_frames=3)
    with GrabThread(src) as grab:
        assert grab.wait_for_new(0, timeout_s=WAIT_S) is not None
        deadline = time.monotonic() + WAIT_S
        while grab.is_alive and time.monotonic() < deadline:
            time.sleep(0.01)
        assert grab.wait_for_new(99, timeout_s=WAIT_S) is None


def test_starting_twice_is_an_explicit_error(source: SyntheticEquirectSource) -> None:
    with GrabThread(source) as grab:
        with pytest.raises(RuntimeError, match="already started"):
            grab.start()


def test_synthetic_source_paces_itself_at_the_requested_fps() -> None:
    src = SyntheticEquirectSource(width=SMALL_W, height=SMALL_H, fps=50.0, max_frames=10)
    started = time.monotonic()
    frames = [src.read() for _ in range(10)]
    elapsed = time.monotonic() - started
    src.close()

    assert all(frame is not None for frame in frames)
    assert src.read() is None
    # 10 frames at 50 fps is 0.2 s; allow slack but reject "as fast as possible".
    assert 0.12 <= elapsed <= 0.6


def test_synthetic_frames_are_equirect_2to1_with_a_burned_in_counter() -> None:
    src = SyntheticEquirectSource(width=SMALL_W, height=SMALL_H, fps=200.0, max_frames=2)
    first = src.read()
    second = src.read()
    src.close()

    assert first is not None and second is not None
    assert first.image.shape == (SMALL_H, SMALL_W, 3)
    assert first.image.dtype.name == "uint8"
    assert second.t_arrival_laptop_ms >= first.t_arrival_laptop_ms
    # The burned-in ms counter differs between two frames taken 5 ms apart.
    assert not (first.image == second.image).all()
