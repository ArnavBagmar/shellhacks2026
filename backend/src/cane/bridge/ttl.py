"""Bridge-side TTL table, identical to ``docs/camera-sync-design.md`` Sec 4.4.

TTL is how long a warning stays meaningful, and it is per class: a cyclist's
bearing is stale in half a second, a bollard's is good for eight.
"""

from __future__ import annotations

from types import MappingProxyType
from typing import Mapping

from cane.contracts.awareness_event import ObjectClass

STATIC_TTL_MS = 8000
PERSON_TTL_MS = 1500
FAST_RIDER_TTL_MS = 500
VEHICLE_TTL_MS = 400
UNKNOWN_TTL_MS = 1500

TTL_MS_BY_CLASS: Mapping[ObjectClass, int] = MappingProxyType(
    {
        ObjectClass.PERSON: PERSON_TTL_MS,
        ObjectClass.CYCLIST: FAST_RIDER_TTL_MS,
        ObjectClass.SCOOTER: FAST_RIDER_TTL_MS,
        ObjectClass.CAR: VEHICLE_TTL_MS,
        ObjectClass.BUS: VEHICLE_TTL_MS,
        ObjectClass.POLE: STATIC_TTL_MS,
        ObjectClass.BOLLARD: STATIC_TTL_MS,
        ObjectClass.WALL: STATIC_TTL_MS,
        ObjectClass.PARKED_CAR: STATIC_TTL_MS,
        ObjectClass.DOOR: STATIC_TTL_MS,
        ObjectClass.OVERHANG: STATIC_TTL_MS,
        ObjectClass.UNKNOWN_OBSTACLE: UNKNOWN_TTL_MS,
    }
)


def ttl_ms_for(cls: ObjectClass) -> int:
    """TTL for a class. The enum is closed, so a miss is a programming error."""
    try:
        return TTL_MS_BY_CLASS[cls]
    except KeyError as exc:  # pragma: no cover - unreachable while the enum is closed
        raise ValueError(f"no TTL defined for class {cls!r}") from exc
