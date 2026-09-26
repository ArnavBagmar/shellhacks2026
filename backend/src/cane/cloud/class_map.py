"""COCO name -> the contract's closed 12-value class, plus the box height each
range estimate is scored against (step-07-design A5).

Two rules that look similar and are not:

1. ``contracts/README.md`` Sec 5 governs what a producer may *emit* -- a
   detector class outside the 12 must be mapped, never invented. It does not
   oblige a producer to emit everything it sees, so :data:`COCO_TO_CLASS` is a
   deliberate keep-list: a name that is absent is dropped, not mapped to
   ``unknown_obstacle``. Eighty COCO classes of kitchenware would otherwise
   become eighty hazards.
2. :data:`HEIGHT_M_BY_COCO` overrides the class default with the height of the
   *box the detector actually draws*. A truck scored at a car's 1.5 m reports
   ~1.7x its true range; a stop sign scored at a pole's 3.0 m is 4x too far and
   would never warn.
"""

from __future__ import annotations

from types import MappingProxyType
from typing import Final, Mapping

from cane.contracts.awareness_event import ObjectClass

# Keep-list. Anything absent is dropped by `map_coco`.
# `traffic light` is deliberately absent: its box is the overhead lamp housing,
# never a walkable hazard. Do not "fix" this by adding it.
COCO_TO_CLASS: Final[Mapping[str, ObjectClass]] = MappingProxyType(
    {
        "person": ObjectClass.PERSON,
        "bicycle": ObjectClass.CYCLIST,
        "motorcycle": ObjectClass.SCOOTER,
        "car": ObjectClass.CAR,
        "truck": ObjectClass.CAR,
        "bus": ObjectClass.BUS,
        "train": ObjectClass.BUS,
        "fire hydrant": ObjectClass.BOLLARD,
        "stop sign": ObjectClass.POLE,
        "parking meter": ObjectClass.POLE,
        "bench": ObjectClass.UNKNOWN_OBSTACLE,
        "dog": ObjectClass.UNKNOWN_OBSTACLE,
        "potted plant": ObjectClass.UNKNOWN_OBSTACLE,
        "chair": ObjectClass.UNKNOWN_OBSTACLE,
        "suitcase": ObjectClass.UNKNOWN_OBSTACLE,
    }
)

# Typical real-world height of the whole object, per emitted class (brief F4).
HEIGHT_M_BY_CLASS: Final[Mapping[ObjectClass, float]] = MappingProxyType(
    {
        ObjectClass.PERSON: 1.7,
        ObjectClass.CYCLIST: 1.8,
        ObjectClass.SCOOTER: 1.6,
        ObjectClass.CAR: 1.5,
        ObjectClass.BUS: 3.0,
        ObjectClass.POLE: 3.0,
        ObjectClass.BOLLARD: 0.9,
        ObjectClass.WALL: 2.5,
        ObjectClass.PARKED_CAR: 1.5,
        ObjectClass.DOOR: 2.1,
        ObjectClass.OVERHANG: 2.5,
        ObjectClass.UNKNOWN_OBSTACLE: 1.5,
    }
)

# Per-COCO overrides: the height of the BOX, which is what `h_px` measures.
HEIGHT_M_BY_COCO: Final[Mapping[str, float]] = MappingProxyType(
    {
        "truck": 2.5,
        "train": 3.5,
        "stop sign": 0.75,
        "parking meter": 1.2,
        "fire hydrant": 0.8,
        "bench": 0.5,
        "dog": 0.6,
        "potted plant": 0.7,
        "chair": 0.9,
        "suitcase": 0.6,
    }
)


def _key(name: str) -> str:
    """Detector class names arrive from ``model.names``; normalise defensively."""
    return name.strip().lower()


def map_coco(name: str) -> ObjectClass | None:
    """The emitted class for a COCO name, or ``None`` to drop the detection."""
    return COCO_TO_CLASS.get(_key(name))


def height_m(coco_name: str, cls: ObjectClass) -> float:
    """Box height in metres: the COCO override if there is one, else the class
    default. Never zero -- the caller divides by it.
    """
    override = HEIGHT_M_BY_COCO.get(_key(coco_name))
    return override if override is not None else HEIGHT_M_BY_CLASS[cls]
