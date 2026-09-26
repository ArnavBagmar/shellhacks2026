"""The health monitor: rung stepping and the silent flip to local (E2).

`docs/camera-sync-design.md` Sec 3.4 in code:

    step DOWN a rung when   p95 round-trip > 500 ms over the last 10 frames
    flip to LOCAL when      3 consecutive timeouts
                        OR  already on rung 4 and still failing
                        OR  the phone's stale-drop rate crosses threshold
    recover                 step up one rung after 30 s healthy
    user-visible            nothing. Silent, logged.

Everything here is pure and immutable: `observe*` takes the clock as an
argument, returns a **new** `HealthState`, and the only side effect is the
one INFO line per transition that rule 7 requires. That is what lets the
120-second recovery ladder be a microsecond-long unit test rather than a
two-minute one.

Two names that read backwards until you look at Sec 3.2's table: `MIN_RUNG`
is rung **4** (the smallest uplink) and `MAX_RUNG` is rung **1** (the
largest). Stepping *down* a rung raises the index.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field, replace
from enum import StrEnum
from typing import Final

from cane.bridge.rungs import Rung, rung_by_index
from cane.transport.messages import PhoneHealth

logger = logging.getLogger(__name__)

RTT_WINDOW: Final = 10
STEP_DOWN_P95_MS: Final = 500
TIMEOUTS_TO_LOCAL: Final = 3
#: Identical to the phone's `STALE_DROP_RATE_THRESHOLD` (step-09-design A14).
STALE_DROP_RATE_THRESHOLD: Final = 0.2
STALE_MIN_BUCKETS: Final = 2
RECOVER_HEALTHY_S: Final = 30
PROBE_INTERVAL_S: Final = 1.0
MIN_RUNG: Final = 4
MAX_RUNG: Final = 1
#: A report older than this is a dead signal, and a dead signal must not
#: drive a switch. Applied symmetrically so a phone clock running a little
#: ahead cannot produce a "future" report that is silently trusted forever.
PHONE_HEALTH_MAX_AGE_MS: Final = 3000
MAX_TRANSITIONS: Final = 32
MS_PER_S: Final = 1000
RECOVER_HEALTHY_MS: Final = RECOVER_HEALTHY_S * MS_PER_S
#: The nearest-rank p95 of a full 10-sample window is its second largest
#: (task-8-brief E2 rule 1), which is why it is defined for that width only.
P95_OFFSET_FROM_TOP: Final = 2


class ModeKind(StrEnum):
    CLOUD = "cloud"
    LOCAL = "local"


@dataclass(frozen=True, slots=True)
class Mode:
    """Who answers, and at which rung.

    A flip carries the rung with it: the crops keep their size, quality and
    rate across the switch, because only the *answerer* changed. Recovery
    re-enters the cloud at `MIN_RUNG` and climbs from there.
    """

    kind: ModeKind
    rung: int

    @staticmethod
    def cloud(rung: int) -> Mode:
        return Mode(ModeKind.CLOUD, _valid_rung(rung))

    @staticmethod
    def local(rung: int = MIN_RUNG) -> Mode:
        return Mode(ModeKind.LOCAL, _valid_rung(rung))

    @property
    def is_local(self) -> bool:
        return self.kind is ModeKind.LOCAL

    def __str__(self) -> str:
        return "local" if self.is_local else f"cloud({self.rung})"


def _valid_rung(rung: int) -> int:
    if not MAX_RUNG <= rung <= MIN_RUNG:
        raise ValueError(f"rung must be {MAX_RUNG}..{MIN_RUNG}, got {rung}")
    return rung


@dataclass(frozen=True, slots=True)
class Ok:
    """The cloud answered inside the backend's budget."""

    rtt_ms: float


@dataclass(frozen=True, slots=True)
class Timeout:
    """No answer inside the budget."""


@dataclass(frozen=True, slots=True)
class Error:
    """An answer, but not a usable one. `status` is `None` for "never
    reached a server" (a refused connection). Counts as a timeout for the
    three-strike rule: a 5xx is also "no answer in time".
    """

    status: int | None = None


Outcome = Ok | Timeout | Error


@dataclass(frozen=True, slots=True)
class Transition:
    from_mode: Mode
    to_mode: Mode
    reason: str
    at_ms: int


@dataclass(frozen=True, slots=True)
class HealthCounters:
    """What the pipeline's 5-second stats line reports."""

    steps_down: int = 0
    steps_up: int = 0
    flips_to_local: int = 0
    recoveries: int = 0
    probes_ok: int = 0
    probes_unhealthy: int = 0

    def plus(self, **deltas: int) -> HealthCounters:
        return replace(self, **{k: getattr(self, k) + v for k, v in deltas.items()})


@dataclass(frozen=True, slots=True)
class HealthState:
    """One immutable snapshot of what the monitor believes."""

    mode: Mode
    rtt_window: tuple[float, ...] = ()
    consecutive_timeouts: int = 0
    healthy_since_ms: int | None = None
    last_step_ms: int | None = None
    last_phone_health: PhoneHealth | None = None
    transitions: tuple[Transition, ...] = ()
    counters: HealthCounters = field(default_factory=HealthCounters)

    @property
    def rung(self) -> int:
        return self.mode.rung


def p95_nearest_rank(window: tuple[float, ...]) -> float | None:
    """The second largest of ten samples, or `None` for a partial window.

    Deliberately not a general percentile: rule 1 fixes both the width and
    the rank, and a window that has not filled must never be able to step a
    rung -- otherwise one slow frame after a step would step again.
    """
    if len(window) < RTT_WINDOW:
        return None
    return sorted(window)[-P95_OFFSET_FROM_TOP]


def phone_reports_unhealthy(health: PhoneHealth) -> bool:
    """Rule 4's predicate, matching the phone's `isStaleDropRateUnhealthy`."""
    return (
        health.received >= 1
        and health.complete_buckets >= STALE_MIN_BUCKETS
        and health.stale_drop_rate >= STALE_DROP_RATE_THRESHOLD
    )


def _is_fresh(health: PhoneHealth, now_ms: int, phone_offset_ms: int) -> bool:
    return abs(now_ms + phone_offset_ms - health.t_phone_ms) <= PHONE_HEALTH_MAX_AGE_MS


class HealthMonitor:
    """Holds the current `HealthState` and replaces it wholesale."""

    def __init__(self, mode: Mode | None = None) -> None:
        self._state = HealthState(mode=mode if mode is not None else Mode.cloud(MAX_RUNG))

    @property
    def state(self) -> HealthState:
        return self._state

    @property
    def mode(self) -> Mode:
        return self._state.mode

    @property
    def is_local(self) -> bool:
        return self._state.mode.is_local

    @property
    def rung(self) -> Rung:
        """The operating point the pipeline must use for the next tick."""
        return rung_by_index(self._state.mode.rung)

    # -- inputs ------------------------------------------------------------

    def observe(self, outcome: Outcome, now_ms: int) -> HealthState:
        """One uplink outcome. Ignored while local: the local backend's own
        latency says nothing about the cloud, and only probes may recover.
        """
        state = self._state
        if state.mode.is_local:
            return state
        if isinstance(outcome, Ok):
            return self._store(self._observe_ok(state, outcome.rtt_ms, now_ms))
        return self._store(self._observe_failure(state, now_ms))

    def observe_phone_health(
        self, health: PhoneHealth, now_ms: int, phone_offset_ms: int = 0
    ) -> HealthState:
        """Rule 4. `phone_offset_ms` is the phone clock minus the laptop
        clock, so the freshness test happens in one domain.
        """
        state = replace(self._state, last_phone_health=health)
        if state.mode.is_local or not _is_fresh(health, now_ms, phone_offset_ms):
            return self._store(state)
        if not phone_reports_unhealthy(health):
            return self._store(state)
        return self._store(
            self._transition(
                state,
                Mode.local(state.mode.rung),
                f"stale_drop_rate={health.stale_drop_rate:.2f}",
                now_ms,
                flips_to_local=1,
            )
        )

    def observe_probe(
        self, ok: bool, rtt_ms: float | None, now_ms: int
    ) -> HealthState:
        """Rule 5. Only meaningful while local; a probe taken in cloud mode
        is discarded rather than counted, so the numbers on the stats line
        mean "while we were falling back".
        """
        state = self._state
        if not state.mode.is_local:
            return state
        healthy = ok and rtt_ms is not None and rtt_ms <= STEP_DOWN_P95_MS
        if not healthy:
            return self._store(
                replace(
                    state,
                    healthy_since_ms=None,
                    counters=state.counters.plus(probes_unhealthy=1),
                )
            )
        since = state.healthy_since_ms if state.healthy_since_ms is not None else now_ms
        state = replace(
            state, healthy_since_ms=since, counters=state.counters.plus(probes_ok=1)
        )
        if now_ms - since < RECOVER_HEALTHY_MS or self._phone_blocks(state, now_ms):
            return self._store(state)
        return self._store(
            self._transition(
                state,
                Mode.cloud(MIN_RUNG),
                f"probe_healthy_{RECOVER_HEALTHY_S}s",
                now_ms,
                recoveries=1,
            )
        )

    # -- rules -------------------------------------------------------------

    def _observe_ok(self, state: HealthState, rtt_ms: float, now_ms: int) -> HealthState:
        window = (state.rtt_window + (rtt_ms,))[-RTT_WINDOW:]
        state = replace(
            state,
            rtt_window=window,
            consecutive_timeouts=0,
            healthy_since_ms=(
                state.healthy_since_ms if state.healthy_since_ms is not None else now_ms
            ),
        )
        p95 = p95_nearest_rank(window)
        if p95 is None:
            return state
        if p95 > STEP_DOWN_P95_MS:
            return self._step_down(state, p95, now_ms)
        return self._maybe_step_up(state, now_ms)

    def _observe_failure(self, state: HealthState, now_ms: int) -> HealthState:
        """Rule 2: a `Timeout` and an `Error` are the same event here."""
        strikes = state.consecutive_timeouts + 1
        state = replace(state, consecutive_timeouts=strikes, healthy_since_ms=None)
        if strikes < TIMEOUTS_TO_LOCAL:
            return state
        return self._transition(
            state,
            Mode.local(state.mode.rung),
            f"consecutive_timeouts={strikes}",
            now_ms,
            flips_to_local=1,
        )

    def _step_down(self, state: HealthState, p95: float, now_ms: int) -> HealthState:
        """Rule 1, and rule 3 when there is no rung left to step to."""
        if state.mode.rung >= MIN_RUNG:
            return self._transition(
                state,
                Mode.local(state.mode.rung),
                f"rung{MIN_RUNG}_p95_rtt_ms={p95:.0f}",
                now_ms,
                flips_to_local=1,
            )
        return self._transition(
            state,
            Mode.cloud(state.mode.rung + 1),
            f"p95_rtt_ms={p95:.0f}",
            now_ms,
            steps_down=1,
        )

    def _maybe_step_up(self, state: HealthState, now_ms: int) -> HealthState:
        """Rule 5's upward half: 30 s clean, and never twice inside 30 s."""
        if state.mode.rung <= MAX_RUNG or state.healthy_since_ms is None:
            return state
        if now_ms - state.healthy_since_ms < RECOVER_HEALTHY_MS:
            return state
        if state.last_step_ms is not None and now_ms - state.last_step_ms < RECOVER_HEALTHY_MS:
            return state
        return self._transition(
            state,
            Mode.cloud(state.mode.rung - 1),
            f"healthy_{RECOVER_HEALTHY_S}s",
            now_ms,
            steps_up=1,
        )

    @staticmethod
    def _phone_blocks(state: HealthState, now_ms: int) -> bool:
        """Rule 6: do not climb back into a cloud the phone is still
        drowning in. A report too old to be trusted blocks nothing -- the
        same reason a stale report cannot trigger rule 4.
        """
        health = state.last_phone_health
        if health is None or not _is_fresh(health, now_ms, 0):
            return False
        return phone_reports_unhealthy(health)

    # -- plumbing ----------------------------------------------------------

    def _transition(
        self, state: HealthState, to_mode: Mode, reason: str, now_ms: int, **counters: int
    ) -> HealthState:
        """Rule 7: exactly one INFO line, and the window starts again.

        The window is cleared on every transition because the samples in it
        were measured at a different operating point; keeping them would let
        one bad rung's RTTs step the next one immediately.
        """
        logger.info("health: %s -> %s reason=%s", state.mode, to_mode, reason)
        return replace(
            state,
            mode=to_mode,
            rtt_window=(),
            consecutive_timeouts=0,
            # A flip to local starts with *no* healthy history: rule 5 wants
            # 30 s of actual probes, not 30 s of having been away.
            healthy_since_ms=None if to_mode.is_local else now_ms,
            last_step_ms=now_ms,
            transitions=(
                state.transitions
                + (Transition(state.mode, to_mode, reason, now_ms),)
            )[-MAX_TRANSITIONS:],
            counters=state.counters.plus(**counters),
        )

    def _store(self, state: HealthState) -> HealthState:
        self._state = state
        return state
