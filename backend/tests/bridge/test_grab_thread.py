"""The dedicated grab thread (F2 rule 1): a slow consumer still gets fresh frames.

The acceptance shape here mirrors the real pipeline: the source produces at
30 fps, the consumer works for 200 ms per tick, and the frames it picks up
must still be fresh -- p95 age under the brief's 40 ms.

Why p95 and not max: a 30 fps source pushes every 33.3 ms and the consumer
picks up at a uniformly random phase, so an age near 33 ms is the *expected*
best case, not a defect; a hard max < 40 ms leaves 7 ms for scheduler jitter
and is flaky by construction. The bug this guards against -- a queue instead
of a single slot -- shows up as ages of 100-300 ms, which both the p95 bound
and the max bound below reject by a wide margin.
"""

from __future__ import annotations

import time

import pytest

from cane.bridge.capture.grab_thread import GrabThread
from cane.bridge.capture.synthetic_source import (
    SyntheticEquirectSource,
    make_equirect_frame,
)
from cane.bridge.metrics import percentile

SOURCE_FPS = 30.0
SMALL_W = 160
SMALL_H = 80
CONSUMER_WORK_S = 0.2
PICKUPS = 10
P95_AGE_AT_PICKUP_MS = 40
SOURCE_PERIOD_MS = 1000.0 / SOURCE_FPS
SCHEDULER_JITTER_ALLOWANCE_MS = 20
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
        # One warm-up pickup: thread start-up is a transient the running
        # pipeline never sees, and it is not what "newest frame only" means.
        warmup = grab.wait_for_new(0, timeout_s=WAIT_S)
        assert warmup is not None
        last_seq = warmup.seq
        for _ in range(PICKUPS):
            time.sleep(CONSUMER_WORK_S)
            frame = grab.wait_for_new(last_seq, timeout_s=WAIT_S)
            assert frame is not None
            ages.append(int(time.time() * 1000) - frame.t_arrival_laptop_ms)
            last_seq = frame.seq

    assert len(ages) == PICKUPS
    p95 = percentile([float(age) for age in ages], 95)
    assert p95 is not None and p95 < P95_AGE_AT_PICKUP_MS, f"stale pickups: {ages}"
    assert max(ages) < SOURCE_PERIOD_MS + SCHEDULER_JITTER_ALLOWANCE_MS, f"stale pickup: {ages}"
    median = percentile([float(age) for age in ages], 50)
    # Uniform phase over one 33 ms frame period: the median must sit near half
    # of it. A queue would park it at hundreds of ms.
    assert median is not None and median < SOURCE_PERIOD_MS


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


def test_the_burned_in_counter_fits_inside_the_frame() -> None:
    """The counter exists to tell frames apart, so it must not run off the edge.

    A full epoch stamp is 13 digits and was 175 px wide in a 160 px frame, which
    clipped the fast-moving low-order digits -- the only ones that change between
    two frames milliseconds apart.
    """
    import cv2

    from cane.bridge.capture.synthetic_source import counter_text, text_scale

    text = counter_text(1_790_443_212_345)
    (width_px, _), _ = cv2.getTextSize(text, cv2.FONT_HERSHEY_SIMPLEX, text_scale(SMALL_W), 2)
    assert width_px < SMALL_W, f"counter {text!r} is {width_px}px wide in a {SMALL_W}px frame"


def test_one_millisecond_changes_the_picture() -> None:
    """Two frames 1 ms apart must be distinguishable, at the smallest size used."""
    a = make_equirect_frame(SMALL_W, SMALL_H, 1_790_443_212_345)
    b = make_equirect_frame(SMALL_W, SMALL_H, 1_790_443_212_346)
    assert not (a == b).all()


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
