"""Bounded rolling percentiles -- the sender's and pipeline's only statistics.

Kept separate so both can report p50/p95 the same way, and so the percentile
definition is testable on its own.
"""

from __future__ import annotations

from collections import deque
from collections.abc import Sequence

DEFAULT_WINDOW = 100
P50 = 50
P95 = 95


def percentile(values: Sequence[float], pct: float) -> float | None:
    """Linear-interpolated percentile; ``None`` for an empty sample.

    ``statistics.quantiles`` needs at least two points and cannot express p95
    directly, so this does the two lines by hand instead.
    """
    if not values:
        return None
    if not 0 <= pct <= 100:
        raise ValueError(f"percentile must be within 0..100, got {pct}")
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    position = (len(ordered) - 1) * pct / 100.0
    lower = int(position)
    upper = min(lower + 1, len(ordered) - 1)
    weight = position - lower
    return ordered[lower] * (1.0 - weight) + ordered[upper] * weight


class RollingWindow:
    """The last ``maxlen`` samples, with p50/p95 on demand."""

    def __init__(self, maxlen: int = DEFAULT_WINDOW) -> None:
        if maxlen <= 0:
            raise ValueError(f"maxlen must be > 0, got {maxlen}")
        self._values: deque[float] = deque(maxlen=maxlen)

    def __len__(self) -> int:
        return len(self._values)

    def add(self, value: float) -> None:
        self._values.append(value)

    @property
    def p50(self) -> float | None:
        return percentile(tuple(self._values), P50)

    @property
    def p95(self) -> float | None:
        return percentile(tuple(self._values), P95)
