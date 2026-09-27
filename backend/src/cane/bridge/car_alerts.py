"""The car-alert switch (``startcane full``): flipped on the live view.

On: cars and buses are detected, drawn, alerted to the phone and every
alarm is saved for replay. Off: every car, bus and parked car is dropped
from each detector response the moment it arrives -- nothing downstream
(the phone's alerts, the page's detection view and hazard board, the
recorder) ever sees one. The detector itself keeps running: steering and
floor drops use the same responses, and people, poles and chairs are
still detected.
"""

from __future__ import annotations

import logging
import os
import threading
from typing import Any

from cane.contracts.awareness_event import ObjectClass

logger = logging.getLogger(__name__)

CAR_ALERTS_ENV = "CANE_CAR_ALERTS"
#: Every contract class that is a vehicle.
VEHICLE_OBJECT_CLASSES = frozenset({ObjectClass.CAR, ObjectClass.BUS, ObjectClass.PARKED_CAR})


class CarAlertSwitch:
    """Thread-safe: the page flips it, the pipeline reads it every response."""

    def __init__(self, on: bool = True) -> None:
        self._lock = threading.Lock()
        self._on = on

    @classmethod
    def from_env(cls) -> CarAlertSwitch:
        """``CANE_CAR_ALERTS=off`` starts with it off; anything else, on."""
        return cls(on=os.environ.get(CAR_ALERTS_ENV, "on").strip().lower() not in ("off", "0", "false", "no"))

    @property
    def on(self) -> bool:
        with self._lock:
            return self._on

    def set(self, on: bool) -> None:
        with self._lock:
            changed = self._on != on
            self._on = on
        if changed:
            logger.info("car alerts %s", "on" if on else "off: vehicles are ignored and nothing is saved")


def without_vehicles(response: Any) -> Any:
    """The same inference response with every vehicle detection removed."""
    kept = tuple(d for d in response.detections if d.cls not in VEHICLE_OBJECT_CLASSES)
    if len(kept) == len(response.detections):
        return response
    return response.model_copy(update={"detections": kept})
