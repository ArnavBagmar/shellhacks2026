"""The health monitor's seven rules (E2), one test each.

Everything here runs on an injected integer clock: the 120-second recovery
ladder costs microseconds, and no test may depend on a sleep. The monitor is
pure -- `observe*` returns a new `HealthState` and never mutates the old one
-- so most assertions are made on returned values, not on the monitor.
"""

from __future__ import annotations

import logging

import pytest

from cane.bridge.health import (
    MAX_RUNG,
    MAX_TRANSITIONS,
    MIN_RUNG,
    PHONE_HEALTH_MAX_AGE_MS,
    RECOVER_HEALTHY_S,
    RTT_WINDOW,
    STALE_DROP_RATE_THRESHOLD,
    STALE_MIN_BUCKETS,
    STEP_DOWN_P95_MS,
    TIMEOUTS_TO_LOCAL,
    Error,
    HealthMonitor,
    Mode,
    Ok,
    Timeout,
    p95_nearest_rank,
)
from cane.transport.messages import PhoneHealth

T0 = 1_790_000_000_000
S = 1000
FAST_MS = 120.0
SLOW_MS = 700.0
LOG_PREFIX = "health: "


def _monitor(rung: int = 1) -> HealthMonitor:
    return HealthMonitor(Mode.cloud(rung))


def _feed(monitor: HealthMonitor, rtt_ms: float, count: int, t0: int = T0, step: int = 100):
    for index in range(count):
        monitor.observe(Ok(rtt_ms), t0 + index * step)
    return monitor.state


def _phone(rate: float, buckets: int = 2, received: int = 10, t_phone_ms: int = T0) -> PhoneHealth:
    return PhoneHealth(
        t_phone_ms=t_phone_ms,
        window_ms=10_000,
        received=received,
        stale_dropped=round(received * rate),
        stale_drop_rate=rate,
        complete_buckets=buckets,
    )


# --------------------------------------------------------------------------
# p95 and the constants
# --------------------------------------------------------------------------


def test_p95_over_ten_samples_is_the_second_largest() -> None:
    window = (10.0, 900.0, 20.0, 30.0, 40.0, 50.0, 60.0, 70.0, 80.0, 612.0)

    assert p95_nearest_rank(window) == 612.0


def test_p95_is_undefined_until_the_window_is_full() -> None:
    assert p95_nearest_rank(()) is None
    assert p95_nearest_rank(tuple(float(i) for i in range(RTT_WINDOW - 1))) is None


def test_the_constants_match_the_design_document() -> None:
    assert (RTT_WINDOW, STEP_DOWN_P95_MS, TIMEOUTS_TO_LOCAL) == (10, 500, 3)
    assert (STALE_DROP_RATE_THRESHOLD, STALE_MIN_BUCKETS) == (0.2, 2)
    assert (RECOVER_HEALTHY_S, MIN_RUNG, MAX_RUNG) == (30, 4, 1)


# --------------------------------------------------------------------------
# Rule 1 -- step down on p95 > 500 ms over ten frames
# --------------------------------------------------------------------------


def test_rule1_ten_slow_frames_step_down_one_rung() -> None:
    monitor = _monitor(rung=1)

    state = _feed(monitor, SLOW_MS, RTT_WINDOW)

    assert state.mode == Mode.cloud(2)
    assert state.counters.steps_down == 1


def test_rule1_nine_slow_frames_are_not_enough() -> None:
    monitor = _monitor(rung=1)

    state = _feed(monitor, SLOW_MS, RTT_WINDOW - 1)

    assert state.mode == Mode.cloud(1)
    assert len(state.rtt_window) == RTT_WINDOW - 1


def test_rule1_a_fast_p95_never_steps_down() -> None:
    monitor = _monitor(rung=1)

    state = _feed(monitor, FAST_MS, RTT_WINDOW * 3)

    assert state.mode == Mode.cloud(1)
    assert state.counters.steps_down == 0


def test_rule1_one_outlier_in_ten_does_not_step_down() -> None:
    """p95 is the second largest, so a single 900 ms spike is absorbed."""
    monitor = _monitor(rung=1)
    for index in range(RTT_WINDOW - 1):
        monitor.observe(Ok(FAST_MS), T0 + index * 100)

    state = monitor.observe(Ok(2000.0), T0 + RTT_WINDOW * 100)

    assert state.mode == Mode.cloud(1)


def test_rule1_the_window_is_cleared_after_a_step_so_it_cannot_step_twice() -> None:
    monitor = _monitor(rung=1)

    state = _feed(monitor, SLOW_MS, RTT_WINDOW)
    assert state.rtt_window == ()

    # Nine more slow frames: still one short of a second step.
    state = _feed(monitor, SLOW_MS, RTT_WINDOW - 1, t0=T0 + 10 * S)
    assert state.mode == Mode.cloud(2)


# --------------------------------------------------------------------------
# Rule 2 -- three consecutive timeouts flip to local
# --------------------------------------------------------------------------


def test_rule2_three_consecutive_timeouts_flip_to_local() -> None:
    monitor = _monitor(rung=1)

    for index in range(TIMEOUTS_TO_LOCAL):
        state = monitor.observe(Timeout(), T0 + index * 300)

    assert state.mode.is_local
    assert state.counters.flips_to_local == 1


def test_rule2_an_ok_resets_the_consecutive_count() -> None:
    monitor = _monitor(rung=1)
    monitor.observe(Timeout(), T0)
    monitor.observe(Timeout(), T0 + 300)
    monitor.observe(Ok(FAST_MS), T0 + 600)

    state = monitor.observe(Timeout(), T0 + 900)

    assert state.mode == Mode.cloud(1)
    assert state.consecutive_timeouts == 1


def test_rule2_an_error_counts_as_a_timeout() -> None:
    """A 5xx is also "no answer in time"."""
    monitor = _monitor(rung=1)
    monitor.observe(Timeout(), T0)
    monitor.observe(Error(503), T0 + 300)

    state = monitor.observe(Error(500), T0 + 600)

    assert state.mode.is_local
    # Two errors and one timeout added up to the three-strike flip, and the
    # strike count starts clean for the next cloud stint.
    assert state.transitions[-1].reason == f"consecutive_timeouts={TIMEOUTS_TO_LOCAL}"
    assert state.consecutive_timeouts == 0


# --------------------------------------------------------------------------
# Rule 3 -- rung 4 and still failing
# --------------------------------------------------------------------------


def test_rule3_rung_four_still_failing_flips_to_local() -> None:
    monitor = _monitor(rung=MIN_RUNG)

    state = _feed(monitor, SLOW_MS, RTT_WINDOW)

    assert state.mode.is_local
    assert state.counters.flips_to_local == 1
    assert state.counters.steps_down == 0


# --------------------------------------------------------------------------
# Rule 4 -- the phone's stale-drop signal
# --------------------------------------------------------------------------


def test_rule4_a_fresh_unhealthy_phone_report_flips_to_local() -> None:
    monitor = _monitor(rung=1)

    state = monitor.observe_phone_health(_phone(0.25), T0 + 1000)

    assert state.mode.is_local
    assert state.counters.flips_to_local == 1


def test_rule4_the_threshold_is_inclusive() -> None:
    monitor = _monitor(rung=1)

    state = monitor.observe_phone_health(_phone(STALE_DROP_RATE_THRESHOLD), T0)

    assert state.mode.is_local


def test_rule4_a_stale_report_is_ignored() -> None:
    """A dead signal must not drive a switch."""
    monitor = _monitor(rung=1)

    state = monitor.observe_phone_health(
        _phone(0.9), T0 + PHONE_HEALTH_MAX_AGE_MS + 1
    )

    assert state.mode == Mode.cloud(1)
    assert state.counters.flips_to_local == 0


def test_rule4_fewer_than_two_complete_buckets_is_ignored() -> None:
    monitor = _monitor(rung=1)

    state = monitor.observe_phone_health(_phone(0.9, buckets=STALE_MIN_BUCKETS - 1), T0)

    assert state.mode == Mode.cloud(1)


def test_rule4_a_report_with_no_receives_is_ignored() -> None:
    monitor = _monitor(rung=1)

    state = monitor.observe_phone_health(_phone(0.0, received=0), T0)

    assert state.mode == Mode.cloud(1)


def test_rule4_the_phone_clock_offset_is_applied_before_the_freshness_test() -> None:
    """`t_phone_ms` is phone-clock; `now_ms` is laptop-clock."""
    monitor = _monitor(rung=1)
    offset_ms = 5000  # the phone's clock runs 5 s ahead of the laptop's

    state = monitor.observe_phone_health(
        _phone(0.9, t_phone_ms=T0 + offset_ms), T0, phone_offset_ms=offset_ms
    )

    assert state.mode.is_local


# --------------------------------------------------------------------------
# Rule 5 -- recovery
# --------------------------------------------------------------------------


def _flip_to_local(monitor: HealthMonitor, at_ms: int = T0) -> None:
    for index in range(TIMEOUTS_TO_LOCAL):
        monitor.observe(Timeout(), at_ms + index)
    assert monitor.state.mode.is_local


def _probe_for(monitor: HealthMonitor, seconds: int, t0: int, ok: bool = True) -> None:
    for second in range(seconds + 1):
        monitor.observe_probe(ok, FAST_MS if ok else None, t0 + second * S)


def test_rule5_recovery_needs_thirty_seconds_of_continuous_healthy_probes() -> None:
    monitor = _monitor(rung=1)
    _flip_to_local(monitor)

    _probe_for(monitor, RECOVER_HEALTHY_S - 1, T0 + S)
    assert monitor.state.mode.is_local

    state = monitor.observe_probe(True, FAST_MS, T0 + S + RECOVER_HEALTHY_S * S)
    assert state.mode == Mode.cloud(MIN_RUNG)
    assert state.counters.recoveries == 1


def test_rule5_one_unhealthy_probe_resets_the_healthy_clock() -> None:
    monitor = _monitor(rung=1)
    _flip_to_local(monitor)
    _probe_for(monitor, RECOVER_HEALTHY_S - 2, T0 + S)

    monitor.observe_probe(False, None, T0 + S + (RECOVER_HEALTHY_S - 1) * S)
    state = monitor.observe_probe(True, FAST_MS, T0 + S + RECOVER_HEALTHY_S * S)

    assert state.mode.is_local
    assert state.counters.probes_unhealthy == 1


def test_rule5_a_slow_probe_is_not_a_healthy_probe() -> None:
    monitor = _monitor(rung=1)
    _flip_to_local(monitor)

    for second in range(RECOVER_HEALTHY_S + 1):
        state = monitor.observe_probe(True, STEP_DOWN_P95_MS + 1.0, T0 + S + second * S)

    assert state.mode.is_local


def test_rule5_probes_are_ignored_while_the_uplink_is_in_use() -> None:
    monitor = _monitor(rung=2)

    state = monitor.observe_probe(True, FAST_MS, T0)

    assert state.mode == Mode.cloud(2)
    assert state.counters.probes_ok == 0


def test_rule5_the_upward_ladder_runs_at_thirty_second_spacing() -> None:
    """The 120 s acceptance run: cloud(4) -> (3) -> (2) -> (1), never faster."""
    monitor = _monitor(rung=1)
    _flip_to_local(monitor)
    _probe_for(monitor, RECOVER_HEALTHY_S, T0 + S)
    assert monitor.state.mode == Mode.cloud(MIN_RUNG)

    recovered_at = T0 + S + RECOVER_HEALTHY_S * S
    ladder: list[tuple[int, int]] = []
    seen = monitor.state.mode
    # Two frames a second for two minutes, all healthy.
    for tick in range(2 * 120):
        now = recovered_at + tick * 500
        state = monitor.observe(Ok(FAST_MS), now)
        if state.mode != seen:
            ladder.append((state.mode.rung, now))
            seen = state.mode

    assert [rung for rung, _ in ladder] == [3, 2, 1]
    spacing = [ladder[0][1] - recovered_at] + [
        ladder[i][1] - ladder[i - 1][1] for i in range(1, len(ladder))
    ]
    assert all(gap >= RECOVER_HEALTHY_S * S for gap in spacing), spacing
    assert monitor.state.counters.steps_up == 3


def test_rule5_a_timeout_restarts_the_thirty_second_healthy_clock() -> None:
    monitor = HealthMonitor(Mode.cloud(3))
    for tick in range(2 * 25):
        monitor.observe(Ok(FAST_MS), T0 + tick * 500)
    monitor.observe(Timeout(), T0 + 25 * S)

    for tick in range(2 * 20):
        state = monitor.observe(Ok(FAST_MS), T0 + 25 * S + tick * 500)

    assert state.mode == Mode.cloud(3)


def test_rule5_rung_one_is_the_top() -> None:
    monitor = _monitor(rung=MAX_RUNG)

    for tick in range(2 * 120):
        state = monitor.observe(Ok(FAST_MS), T0 + tick * 500)

    assert state.mode == Mode.cloud(MAX_RUNG)
    assert state.counters.steps_up == 0


# --------------------------------------------------------------------------
# Rule 6 -- the phone report gates recovery, and nothing else
# --------------------------------------------------------------------------


def test_rule6_a_healthy_phone_report_alone_does_not_recover() -> None:
    monitor = _monitor(rung=1)
    _flip_to_local(monitor)

    state = monitor.observe_phone_health(_phone(0.0), T0 + S)

    assert state.mode.is_local


def test_rule6_an_unhealthy_phone_report_blocks_recovery() -> None:
    monitor = _monitor(rung=1)
    monitor.observe_phone_health(_phone(0.5), T0)
    assert monitor.state.mode.is_local

    for second in range(RECOVER_HEALTHY_S + 2):
        now = T0 + S + second * S
        monitor.observe_phone_health(_phone(0.5, t_phone_ms=now), now)
        state = monitor.observe_probe(True, FAST_MS, now)
    assert state.mode.is_local

    healthy_at = T0 + S + (RECOVER_HEALTHY_S + 2) * S
    monitor.observe_phone_health(_phone(0.0, t_phone_ms=healthy_at), healthy_at)
    state = monitor.observe_probe(True, FAST_MS, healthy_at)

    assert state.mode == Mode.cloud(MIN_RUNG)


# --------------------------------------------------------------------------
# Rule 7 -- one INFO line per transition, and bounded history
# --------------------------------------------------------------------------


def test_rule7_a_step_down_logs_exactly_one_info_line(
    caplog: pytest.LogCaptureFixture,
) -> None:
    monitor = _monitor(rung=1)

    with caplog.at_level(logging.INFO, logger="cane.bridge.health"):
        _feed(monitor, 612.0, RTT_WINDOW)

    lines = [r.getMessage() for r in caplog.records if r.levelno == logging.INFO]
    assert lines == ["health: cloud(1) -> cloud(2) reason=p95_rtt_ms=612"]


def test_rule7_a_flip_to_local_logs_its_reason(caplog: pytest.LogCaptureFixture) -> None:
    monitor = _monitor(rung=2)

    with caplog.at_level(logging.INFO, logger="cane.bridge.health"):
        _flip_to_local(monitor)

    lines = [r.getMessage() for r in caplog.records if r.levelno == logging.INFO]
    assert lines == ["health: cloud(2) -> local reason=consecutive_timeouts=3"]


def test_rule7_nothing_is_logged_when_nothing_changes(
    caplog: pytest.LogCaptureFixture,
) -> None:
    monitor = _monitor(rung=1)

    with caplog.at_level(logging.INFO, logger="cane.bridge.health"):
        _feed(monitor, FAST_MS, RTT_WINDOW)
        monitor.observe(Timeout(), T0 + 20 * S)
        monitor.observe_phone_health(_phone(0.0), T0 + 21 * S)

    assert [r.getMessage() for r in caplog.records if r.levelno == logging.INFO] == []


def test_rule7_the_transition_history_is_bounded() -> None:
    monitor = _monitor(rung=1)
    now = T0
    for _ in range(MAX_TRANSITIONS + 5):
        _flip_to_local(monitor, now)
        # Recover so the next flip has somewhere to fall from.
        for second in range(RECOVER_HEALTHY_S + 1):
            monitor.observe_probe(True, FAST_MS, now + 10 + second * S)
        now += (RECOVER_HEALTHY_S + 5) * S

    state = monitor.state
    assert len(state.transitions) == MAX_TRANSITIONS
    assert state.counters.flips_to_local > MAX_TRANSITIONS // 2


# --------------------------------------------------------------------------
# Immutability
# --------------------------------------------------------------------------


def test_observing_returns_a_new_state_and_never_mutates_the_old_one() -> None:
    monitor = _monitor(rung=1)
    before = monitor.state

    after = monitor.observe(Ok(FAST_MS), T0)

    assert before is not after
    assert before.rtt_window == ()
    assert after.rtt_window == (FAST_MS,)
    with pytest.raises(AttributeError):
        after.mode = Mode.local()  # type: ignore[misc]


def test_the_monitor_exposes_the_rung_the_pipeline_should_use() -> None:
    monitor = _monitor(rung=1)
    assert monitor.rung.index == 1
    assert monitor.is_local is False

    _feed(monitor, SLOW_MS, RTT_WINDOW)
    assert monitor.rung.index == 2
    assert monitor.rung.crop_px == 640

    _flip_to_local(monitor, T0 + 60 * S)
    assert monitor.is_local is True
    # A flip does not change the frame's size or rate: only who answers.
    assert monitor.rung.index == 2
