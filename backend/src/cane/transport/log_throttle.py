"""At most one log line per interval, for events that fire every tick.

A dropped frame is normal and a dropped frame every tick is a fault, but the
log must not become the fault: at 3 fps an un-throttled warning is three
lines a second, which buries the 5-second stats line that actually carries
the numbers. Every counter is still exact -- only the logging is thinned.

It lives in `cane.transport` for the same reason `wall_clock` does: both the
transport and the bridge need it, and the transport may not import the
bridge (the phone link must stay usable without a pipeline behind it).
"""

from __future__ import annotations

import time

DEFAULT_LOG_INTERVAL_S = 5.0


class LogThrottle:
    """``if throttle.allow(): logger.warning(...)``."""

    def __init__(self, interval_s: float = DEFAULT_LOG_INTERVAL_S) -> None:
        if interval_s < 0:
            raise ValueError(f"interval_s must be >= 0, got {interval_s}")
        self._interval_s = interval_s
        self._last_s: float | None = None

    def allow(self) -> bool:
        """True at most once per interval; the first call always passes."""
        now_s = time.monotonic()
        if self._last_s is not None and now_s - self._last_s < self._interval_s:
            return False
        self._last_s = now_s
        return True
