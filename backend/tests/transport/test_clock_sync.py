"""Pure, immutable offset-estimator tests.

Every case here works with an in-memory :class:`OffsetEstimator` -- no
sockets, no wall-clock reads. See task-3-brief.md D3 and D6, and
camera-sync-design.md Sec 4.2-4.3 for the algorithm these pin down.
"""

import statistics

import pytest

from cane.transport.clock_sync import (
    JUMP_THRESHOLD_MS,
    WINDOW,
    ClockNotSyncedError,
    NtpSample,
    OffsetEstimator,
    PhoneClock,
)


def _sample(t1: int, t2: int, t3: int, t4: int) -> NtpSample:
    return NtpSample(t1=t1, t2=t2, t3=t3, t4=t4)


def test_ntp_sample_known_value_offset_and_rtt():
    sample = _sample(t1=1000, t2=1105, t3=1106, t4=1011)
    assert sample.offset_ms == 100
    assert sample.rtt_ms == 10


def test_empty_estimator_is_not_synced_and_has_no_best():
    est = OffsetEstimator()
    assert est.is_synced is False
    assert est.best is None


def test_single_sample_becomes_best_and_synced():
    est = OffsetEstimator().add(_sample(1000, 1105, 1106, 1011))
    assert est.is_synced is True
    assert est.best is not None
    assert est.offset_ms == 100


def _sample_with_offset_and_rtt(t1: int, offset: int, rtt: int) -> NtpSample:
    """Builds a sample with an *independently* chosen ``offset_ms`` and
    ``rtt_ms``, unlike a naive construction where ``t3 = t4 + offset``
    makes ``rtt_ms`` collapse to a value that depends on ``offset`` (and
    can accidentally come out identical for every sample -- see the
    docstring on the test this feeds).

    Splits the round trip symmetrically (``rtt // 2`` each way, zero
    processing time at the phone), which makes ``offset_ms`` land exactly
    on ``offset`` and ``rtt_ms`` exactly on ``rtt`` for any even ``rtt``.
    """
    assert rtt % 2 == 0, "rtt must be even for the symmetric split below to be exact"
    t4 = t1 + rtt
    t2 = t1 + rtt // 2 + offset
    t3 = t2
    return NtpSample(t1=t1, t2=t2, t3=t3, t4=t4)


def test_lowest_rtt_sample_wins_over_jittery_offsets_not_median():
    """D3/D6: "use the sample with the lowest RTT -- not the median of the
    offsets." This must fail if `OffsetEstimator.best` is ever changed to
    pick the median (or the mean, or the most recent) instead.

    The true sample is added FIRST (so a correct implementation is not
    accidentally rescued by the "ties -> most recent" rule) and carries
    the lowest RTT by a wide, independently-chosen margin; every jittery
    sample's offset sits *above* the true one (never below), so the
    median of all nine offsets is unambiguously different from the truth
    -- a median-of-offsets estimator would land there instead, and both
    assertions below would fail.
    """
    true_offset = 50
    true_rtt = 4
    # Independently-chosen RTTs (bearing no relationship to the offsets
    # below): all comfortably worse than true_rtt, and distinct from each
    # other so this test documents a real spread, not one repeated value.
    jitter_rtts = [50, 52, 54, 56, 58, 60, 62, 64]
    # All strictly greater than true_offset (never a symmetric +-30 split)
    # so the median of the 9 offsets cannot coincide with the truth.
    jitter_offsets = [80, 81, 82, 83, 84, 85, 86, 87]

    est = OffsetEstimator()
    est = est.add(_sample_with_offset_and_rtt(t1=2_000_000, offset=true_offset, rtt=true_rtt))
    for i, (offset, rtt) in enumerate(zip(jitter_offsets, jitter_rtts), start=1):
        est = est.add(_sample_with_offset_and_rtt(t1=2_000_000 + i, offset=offset, rtt=rtt))

    assert len(est.samples) == 9

    all_offsets = [s.offset_ms for s in est.samples]
    all_rtts = [s.rtt_ms for s in est.samples]
    # Documents the RTT spread this test relies on: the true sample's RTT
    # is the strict minimum, by a wide margin, over the whole window.
    assert all_rtts == [true_rtt, *jitter_rtts]
    assert min(all_rtts) == true_rtt
    assert all(rtt > true_rtt + 30 for rtt in jitter_rtts)

    median_offset = statistics.median(all_offsets)
    assert median_offset != true_offset, "test setup bug: median must differ from the truth to be a real regression check"

    assert est.offset_ms == true_offset
    assert est.offset_ms != median_offset


def test_window_evicts_oldest_sample_at_ten_additions():
    est = OffsetEstimator()
    base_t1 = 3_000_000
    for i in range(10):
        t1 = base_t1 + i * 1000
        t4 = t1 + 20
        t2 = t1 + 10
        t3 = t4 + 10
        est = est.add(_sample(t1, t2, t3, t4))

    assert len(est.samples) == WINDOW
    # The very first sample (i=0) must have been evicted.
    assert all(s.t1 != base_t1 for s in est.samples)


def test_clock_jump_flushes_window_and_new_sample_wins_immediately():
    est = OffsetEstimator()
    base_t1 = 4_000_000
    for i in range(9):
        t1 = base_t1 + i * 1000
        t4 = t1 + 20
        t2 = t1 + 10
        t3 = t4 + 10
        est = est.add(_sample(t1, t2, t3, t4))
    assert len(est.samples) == 9
    assert abs(est.offset_ms - 10) < 1

    # A jump to an offset ~5000 ms away from the stable ~10 ms offset above
    # is far beyond JUMP_THRESHOLD_MS -- the window must
    # flush to just this one sample, not be pinned by the 9 stale ones.
    jump_t1 = base_t1 + 9000
    jump_t4 = jump_t1 + 20
    jump_t2 = jump_t1 + 5000
    jump_t3 = jump_t4 + 5000
    est = est.add(_sample(jump_t1, jump_t2, jump_t3, jump_t4))

    assert est.offset_ms == 5000
    assert len(est.samples) == 1
    assert JUMP_THRESHOLD_MS == 100


def test_negative_rtt_sample_is_discarded_and_estimator_unchanged():
    est = OffsetEstimator().add(_sample(1000, 1105, 1106, 1011))
    before = est
    # rtt_ms = (t4 - t1) - (t3 - t2) = (1005 - 1000) - (1200 - 1000) = -195
    negative_rtt_sample = _sample(t1=1000, t2=1000, t3=1200, t4=1005)
    assert negative_rtt_sample.rtt_ms < 0

    after = est.add(negative_rtt_sample)
    assert after is before
    assert after.samples == before.samples


def test_add_returns_new_instance_and_leaves_old_one_unchanged():
    original = OffsetEstimator()
    updated = original.add(_sample(1000, 1105, 1106, 1011))

    assert updated is not original
    assert original.samples == ()
    assert original.is_synced is False
    assert updated.is_synced is True


def test_phone_clock_capture_time_ms_formula():
    est = OffsetEstimator().add(_sample(t1=1000, t2=1100, t3=1100, t4=1000))
    # offset_ms = ((1100 - 1000) + (1100 - 1000)) / 2 == 100
    assert est.offset_ms == 100

    clock = PhoneClock.from_estimator(est, pipeline_delay_d_ms=40)
    t_arrival_laptop_ms = 9_000_000
    # t_capture_ms = t_arrival_laptop_ms - D_ms + offset_ms
    assert clock.capture_time_ms(t_arrival_laptop_ms) == 9_000_000 - 40 + 100


def test_phone_clock_raises_before_any_sample():
    clock = PhoneClock.from_estimator(OffsetEstimator(), pipeline_delay_d_ms=0)
    with pytest.raises(ClockNotSyncedError):
        clock.capture_time_ms(1_000_000)
