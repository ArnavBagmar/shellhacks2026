"""The dedicated grab thread -- rule 1 of Sec 3.3.

It reads the source in a tight loop forever and drops every frame the
consumer did not pick up. Skip this and the driver's own buffering serves
frames 100-300 ms behind reality, with nothing in the logs to say so.
"""

from __future__ import annotations

import logging
import threading
import time
from types import TracebackType

from cane.bridge.capture.base import Frame, FrameSource
from cane.bridge.capture.latest import LatestFrameStore

logger = logging.getLogger(__name__)

# Consecutive failed reads before the thread gives up and fails the store.
READ_FAILURE_LIMIT = 30
READ_FAILURE_SLEEP_S = 0.05
JOIN_TIMEOUT_S = 2.0


class GrabThread:
    """``with GrabThread(source) as grab: grab.wait_for_new(seq, timeout_s)``."""

    def __init__(
        self,
        source: FrameSource,
        store: LatestFrameStore | None = None,
        thread_name: str = "cane-grab",
    ) -> None:
        self._source = source
        self._store = store if store is not None else LatestFrameStore()
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, name=thread_name, daemon=True)
        self._started = False

    @property
    def frames_grabbed(self) -> int:
        return self._store.pushed_count

    @property
    def is_alive(self) -> bool:
        return self._thread.is_alive()

    @property
    def source_name(self) -> str:
        return self._source.name

    def start(self) -> None:
        if self._started:
            raise RuntimeError(f"GrabThread for {self._source.name!r} is already started")
        self._started = True
        self._thread.start()

    def wait_for_new(self, last_seq: int, timeout_s: float) -> Frame | None:
        return self._store.wait_for_new(last_seq, timeout_s)

    def stop(self, join_timeout_s: float = JOIN_TIMEOUT_S) -> bool:
        """Ask the thread to finish and close the source.

        Returns ``False`` if the thread is still stuck inside ``source.read()``
        after ``join_timeout_s`` -- in that case the source is deliberately
        *not* closed, because closing a capture another thread is blocked
        inside is unsafe.
        """
        self._stop.set()
        if self._started:
            self._thread.join(timeout=join_timeout_s)
            if self._thread.is_alive():
                logger.warning(
                    "grab thread for %s is stuck in read(); abandoning the source",
                    self._source.name,
                )
                return False
        self._source.close()
        return True

    def __enter__(self) -> GrabThread:
        self.start()
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.stop()

    def _run(self) -> None:
        failures = 0
        while not self._stop.is_set():
            try:
                frame = self._source.read()
            except Exception:  # noqa: BLE001 - a source fault must not kill the process
                logger.exception("grab thread: %s.read() raised; failing the store", self._source.name)
                self._store.fail()
                return
            if frame is None:
                failures += 1
                if failures >= READ_FAILURE_LIMIT:
                    logger.error(
                        "grab thread: %s returned no frame %d times; giving up",
                        self._source.name,
                        failures,
                    )
                    self._store.fail()
                    return
                time.sleep(READ_FAILURE_SLEEP_S)
                continue
            failures = 0
            self._store.push(frame.image, frame.t_arrival_laptop_ms)
