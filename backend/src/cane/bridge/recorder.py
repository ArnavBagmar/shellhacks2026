"""Tap the phone uplink and append every event to a JSONL file (step 28).

The recorder is a *measuring instrument*, so its first duty is to not perturb
what it measures. Two rules carry that:

1. It forwards the inner sink's result unchanged. The bridge cannot tell from
   the return value whether a recording is running.
2. A write fault is counted and logged, never raised. Losing a line of a
   baseline recording costs a re-walk; letting an exception out of
   ``send_event`` would cost the wearer a warning, and the whole point of the
   baseline is to make warnings trustworthy.

Why the file and not the existing ``phone-YYYYMMDD.jsonl``: that one carries
only the phone's ``diag`` lines. Nothing in the repo persisted the awareness
stream before this, which is why the trust baseline could not be measured.

``t_emit_ms`` is the laptop's monotonic-ish send stamp, and it is what the
replay tool drives its virtual clock from. ``t_capture_ms`` cannot serve:
it is a *phone*-clock capture stamp, so several events share one value and the
gauntlet's staleness and refractory gates would see no time pass between them.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Callable, Protocol, TextIO

from cane.contracts.awareness_event import AwarenessEvent
from cane.transport.wall_clock import now_ms

logger = logging.getLogger("cane.bridge.recorder")

Clock = Callable[[], int]

EMIT_STAMP_KEY = "t_emit_ms"


class EventSink(Protocol):
    """The one method of ``pipeline.EventSink`` this decorator needs."""

    async def send_event(self, event: AwarenessEvent) -> bool: ...


def open_recording(path: Path) -> TextIO:
    """Open `path` for a new recording, refusing to overwrite one.

    Exclusive creation is deliberate: a baseline walk cannot be repeated under
    the same traffic, so silently truncating yesterday's recording would
    destroy the only copy of an unrepeatable measurement.
    """
    if path.exists():
        raise FileExistsError(
            f"{path} already exists. A baseline walk cannot be re-run under the same "
            "traffic, so this refuses to overwrite it: move it aside or pick another name."
        )
    path.parent.mkdir(parents=True, exist_ok=True)
    # "x" as well as the check above: the check gives the good message, the
    # mode closes the race.
    return path.open("x", encoding="utf-8")


def wrap_event_sink(inner: EventSink, stream: TextIO | None) -> EventSink:
    """`inner` itself when there is no recording.

    Returning the very same object, rather than a pass-through wrapper, is what
    makes "no flag, no change" checkable by identity instead of by reading the
    decorator and believing it.
    """
    return inner if stream is None else RecordingEventSink(inner, stream)


class RecordingEventSink:
    """Appends each event as one JSON line, then forwards it untouched."""

    def __init__(self, inner: EventSink, stream: TextIO, clock: Clock = now_ms) -> None:
        self._inner = inner
        self._stream = stream
        self._clock = clock
        self._write_failures = 0
        self._recorded = 0

    @property
    def recorded(self) -> int:
        """Lines written. The CLI prints it so a walk can be sanity-checked
        before the rig is unpacked."""
        return self._recorded

    @property
    def write_failures(self) -> int:
        return self._write_failures

    async def send_event(self, event: AwarenessEvent) -> bool:
        # Record first: what we set out to send is the honest record, and a
        # failed send is itself a fact the baseline wants.
        self._record(event)
        return await self._inner.send_event(event)

    def _record(self, event: AwarenessEvent) -> None:
        line = {
            **event.model_dump(mode="json", by_alias=True),
            EMIT_STAMP_KEY: self._clock(),
        }
        try:
            self._stream.write(json.dumps(line) + "\n")
            # Per event, so a killed walk keeps everything up to the kill.
            self._stream.flush()
        except (OSError, ValueError) as exc:
            self._write_failures += 1
            logger.warning(
                "dropping a recording line for track_id=%s: %s", event.track_id, exc
            )
            return
        self._recorded += 1
