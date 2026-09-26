"""Single-slot newest-frame store -- rule 1 of Sec 3.3, never a queue.

Ported from ~/Desktop/insta360 (Arnav's prototype); timestamps are integer
milliseconds here and the store hands back a frozen :class:`Frame` instead of
a tuple.

This one mutable cell is intentional and is the only mutable shared state in
the bridge: it exists precisely so that intermediate frames are *dropped*
rather than buffered. Everything it hands out is an immutable value object.
"""

from __future__ import annotations

import threading

import numpy as np

from cane.bridge.capture.base import Frame


class LatestFrameStore:
    """Producers ``push``; the consumer ``wait_for_new``\\ s for a frame newer
    than the sequence number it last saw. Only the newest push survives.
    """

    def __init__(self) -> None:
        self._cond = threading.Condition()
        self._latest: Frame | None = None
        self._seq = 0
        self._pushed = 0
        self._failed = False

    @property
    def pushed_count(self) -> int:
        """Every push ever accepted, including the ones that were overwritten
        before a consumer saw them -- this is the grab-fps numerator.
        """
        with self._cond:
            return self._pushed

    @property
    def failed(self) -> bool:
        with self._cond:
            return self._failed

    def push(self, image: np.ndarray, t_arrival_laptop_ms: int) -> Frame:
        """Replace the slot with a new frame and wake the consumer."""
        with self._cond:
            self._seq += 1
            self._pushed += 1
            frame = Frame(image=image, t_arrival_laptop_ms=t_arrival_laptop_ms, seq=self._seq)
            self._latest = frame
            self._cond.notify_all()
            return frame

    def fail(self) -> None:
        """Wake any waiter and make every future wait return ``None``."""
        with self._cond:
            self._failed = True
            self._cond.notify_all()

    def wait_for_new(self, last_seq: int, timeout_s: float) -> Frame | None:
        """Block for the newest frame with ``seq > last_seq``.

        Returns ``None`` on timeout or once the producer has called
        :meth:`fail` -- never raises, so a consumer loop can treat "no frame"
        as an ordinary tick outcome.
        """
        with self._cond:
            arrived = self._cond.wait_for(
                lambda: self._seq > last_seq or self._failed, timeout=timeout_s
            )
            if not arrived or self._failed or self._latest is None:
                return None
            return self._latest
