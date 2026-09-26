"""The 5-second stats line: every number that would otherwise fail silently.

Split out of `pipeline.py` when step 8 added the health counters -- the
pipeline is about producing frames, and this is about proving it did. The
line is the *only* place a rung step, a flip to local or a failing probe
becomes visible, because Sec 3.4 says the fallback is silent to the wearer.
"""

from __future__ import annotations

from dataclasses import dataclass

from cane.bridge.health import HealthMonitor


@dataclass(frozen=True)
class HealthSnapshot:
    """The monitor's counters, flattened for the stats line."""

    mode: str
    rung: int
    steps_down: int
    steps_up: int
    flips_to_local: int
    recoveries: int
    probes_ok: int
    probes_unhealthy: int


@dataclass(frozen=True)
class PipelineStats:
    """An immutable snapshot of one run (or one 5-second window)."""

    elapsed_s: float
    grabbed: int
    ticks: int
    sent: int
    dropped_busy: int
    skipped_unsynced: int
    skipped_no_frame: int
    skipped_deadline: int
    succeeded: int
    failed: int
    rejected: int
    events_delivered: int
    events_undelivered: int
    max_in_flight: int
    frame_age_p50_ms: float | None
    frame_age_p95_ms: float | None
    rtt_p50_ms: float | None
    rtt_p95_ms: float | None
    #: `None` when the run has no health monitor (a bench run, or any
    #: caller from before step 8).
    health: HealthSnapshot | None = None
    #: The detector the local fallback actually loaded, read off the built
    #: backend -- never re-derived from the CLI flags. `None` when the run
    #: has no local half at all.
    local_detector: str | None = None
    #: Inbound phone frames the transport did not act on.
    frames_ignored: int = 0

    @property
    def grab_fps(self) -> float:
        return self.grabbed / self.elapsed_s if self.elapsed_s > 0 else 0.0

    @property
    def send_fps(self) -> float:
        return self.sent / self.elapsed_s if self.elapsed_s > 0 else 0.0


def health_snapshot(monitor: HealthMonitor | None) -> HealthSnapshot | None:
    """Flatten the monitor's immutable state for one stats window."""
    if monitor is None:
        return None
    state = monitor.state
    counters = state.counters
    return HealthSnapshot(
        mode=str(state.mode),
        rung=state.rung,
        steps_down=counters.steps_down,
        steps_up=counters.steps_up,
        flips_to_local=counters.flips_to_local,
        recoveries=counters.recoveries,
        probes_ok=counters.probes_ok,
        probes_unhealthy=counters.probes_unhealthy,
    )


def _ms(value: float | None) -> str:
    return "n/a" if value is None else f"{value:.0f}ms"


def _health_segment(health: HealthSnapshot) -> str:
    return (
        f" | mode {health.mode} rung {health.rung} | "
        f"steps_down {health.steps_down} steps_up {health.steps_up} "
        f"flips_to_local {health.flips_to_local} recoveries {health.recoveries} | "
        f"probe ok {health.probes_ok} bad {health.probes_unhealthy}"
    )


def _link_segment(stats: PipelineStats) -> str:
    """What the phone link did with what the pipeline produced."""
    segment = f" | frames_ignored {stats.frames_ignored}"
    if stats.local_detector is None:
        return segment
    return f"{segment} local={stats.local_detector}"


def format_stats_line(stats: PipelineStats) -> str:
    """The one-line 5-second summary (F5, extended by E2.7).

    `events N undelivered M`: a delivered count on its own reads like
    success even when every event was produced with no phone attached,
    which is the silent failure this line exists to expose.
    """
    line = (
        f"grab {stats.grab_fps:.1f} fps | send {stats.send_fps:.1f} fps | "
        f"dropped_busy {stats.dropped_busy} | "
        f"frame_age p50 {_ms(stats.frame_age_p50_ms)} p95 {_ms(stats.frame_age_p95_ms)} | "
        f"rtt p50 {_ms(stats.rtt_p50_ms)} p95 {_ms(stats.rtt_p95_ms)} | "
        f"in_flight_max {stats.max_in_flight} | "
        f"events {stats.events_delivered} undelivered {stats.events_undelivered} | "
        f"failed {stats.failed} rejected {stats.rejected} "
        f"unsynced {stats.skipped_unsynced} no_frame {stats.skipped_no_frame} "
        f"deadline {stats.skipped_deadline}"
    ) + _link_segment(stats)
    return line if stats.health is None else line + _health_segment(stats.health)
