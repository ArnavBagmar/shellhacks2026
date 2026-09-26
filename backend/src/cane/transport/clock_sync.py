"""Offset estimator + phone-clock conversion for the NTP-style handshake.

Pure and immutable: no I/O, no wall-clock reads, no mutation. See
``docs/camera-sync-design.md`` Sec 4.2-4.3 and ``task-3-brief.md`` D3 for the
algorithm this implements. ``server.py`` is the only caller that owns a
wall clock or a socket; this module never does.
"""

from __future__ import annotations

from dataclasses import dataclass

# Run the handshake every 5 s (D3); keep at most the last 9 samples, and
# treat any new sample whose offset differs from the current best by more
# than 100 ms as a system clock jump, flushing the window so the estimator
# self-corrects within two handshakes (10 s) instead of being pinned by a
# stale low-RTT sample for up to 45 s.
WINDOW = 9
JUMP_THRESHOLD_MS = 100
PING_INTERVAL_S = 5.0


class ClockNotSyncedError(RuntimeError):
    """Raised by :meth:`PhoneClock.capture_time_ms` before any NtpSample has
    been observed -- there is no offset to convert with yet.
    """


@dataclass(frozen=True)
class NtpSample:
    """One NTP-style four-timestamp exchange, all integer Unix-epoch ms.

    ``t1``/``t4`` are laptop-clock timestamps (send/receive); ``t2``/``t3``
    are phone-clock timestamps (receive/send).
    """

    t1: int
    t2: int
    t3: int
    t4: int

    @property
    def offset_ms(self) -> float:
        """Phone minus laptop: ``((t2 - t1) + (t3 - t4)) / 2``."""
        return ((self.t2 - self.t1) + (self.t3 - self.t4)) / 2

    @property
    def rtt_ms(self) -> int:
        """Round-trip time with phone processing time subtracted out."""
        return (self.t4 - self.t1) - (self.t3 - self.t2)


@dataclass(frozen=True)
class OffsetEstimator:
    """Immutable sliding window of :class:`NtpSample`. ``add`` never mutates
    ``self``; it returns a new estimator.
    """

    samples: tuple[NtpSample, ...] = ()

    @property
    def is_synced(self) -> bool:
        return len(self.samples) > 0

    @property
    def best(self) -> NtpSample | None:
        """The lowest-RTT sample in the window; ties keep the most recent.

        ``min`` keeps the first minimal element it sees, so scanning newest
        to oldest makes a tie resolve to the newest of the tied samples.
        """
        if not self.samples:
            return None
        return min(reversed(self.samples), key=lambda sample: sample.rtt_ms)

    @property
    def offset_ms(self) -> float:
        best = self.best
        if best is None:
            raise ClockNotSyncedError("no NTP sample yet; the estimator has an empty window")
        return best.offset_ms

    def add(self, sample: NtpSample) -> OffsetEstimator:
        """Return a new estimator with ``sample`` folded in.

        A negative-RTT sample (path asymmetry that makes RTT accounting
        nonsensical) is discarded outright -- the estimator comes back
        unchanged. A sample whose offset jumps more than
        ``JUMP_THRESHOLD_MS`` from the current best flushes the window,
        since a stale pre-jump sample would otherwise keep winning
        lowest-RTT for up to ``WINDOW`` handshakes.
        """
        if sample.rtt_ms < 0:
            return self

        current_best = self.best
        if current_best is not None and abs(sample.offset_ms - current_best.offset_ms) > JUMP_THRESHOLD_MS:
            return OffsetEstimator(samples=(sample,))

        return OffsetEstimator(samples=(*self.samples, sample)[-WINDOW:])


@dataclass(frozen=True)
class PhoneClock:
    """Converts a laptop arrival time into phone-clock terms (D1)."""

    estimator: OffsetEstimator
    pipeline_delay_d_ms: int

    @staticmethod
    def from_estimator(estimator: OffsetEstimator, pipeline_delay_d_ms: int) -> PhoneClock:
        return PhoneClock(estimator=estimator, pipeline_delay_d_ms=pipeline_delay_d_ms)

    def capture_time_ms(self, t_arrival_laptop_ms: int) -> int:
        """``t_capture_ms = t_arrival_laptop_ms - D_ms + offset_ms`` (D1).

        Raises :class:`ClockNotSyncedError` if the estimator has no sample
        yet -- there is no offset to apply.
        """
        if not self.estimator.is_synced:
            raise ClockNotSyncedError("no NTP sample yet; cannot convert to phone-clock time")
        return round(t_arrival_laptop_ms - self.pipeline_delay_d_ms + self.estimator.offset_ms)
