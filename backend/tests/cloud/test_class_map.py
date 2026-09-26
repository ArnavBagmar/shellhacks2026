"""COCO name -> ObjectClass, and the box height each estimate uses (A5).

The mapping governs what the cloud *emits*; anything absent from the keep-list
is dropped entirely, which the contract permits (a producer may drop, it may
not invent a class).
"""

from __future__ import annotations

import pytest

from cane.cloud.class_map import (
    COCO_TO_CLASS,
    HEIGHT_M_BY_CLASS,
    HEIGHT_M_BY_COCO,
    height_m,
    map_coco,
)
from cane.contracts.awareness_event import ObjectClass


def test_readme_rows_bicycle_motorcycle_truck() -> None:
    """contracts/README.md Sec 5's three explicit rows."""
    assert map_coco("bicycle") is ObjectClass.CYCLIST
    assert map_coco("motorcycle") is ObjectClass.SCOOTER
    assert map_coco("truck") is ObjectClass.CAR


def test_unmapped_name_returns_none() -> None:
    assert map_coco("banana") is None
    assert map_coco("") is None
    # Deliberately dropped: the box is the overhead lamp housing (A5).
    assert map_coco("traffic light") is None


def test_every_object_class_has_a_height() -> None:
    assert set(HEIGHT_M_BY_CLASS) == set(ObjectClass)
    assert all(h > 0.0 for h in HEIGHT_M_BY_CLASS.values())


def test_coco_height_override_beats_class_default() -> None:
    """A truck mapped to `car` must not be scored at a car's 1.5 m."""
    assert height_m("truck", ObjectClass.CAR) == HEIGHT_M_BY_COCO["truck"]
    assert height_m("truck", ObjectClass.CAR) != HEIGHT_M_BY_CLASS[ObjectClass.CAR]
    assert height_m("car", ObjectClass.CAR) == HEIGHT_M_BY_CLASS[ObjectClass.CAR]


def test_mapping_is_case_insensitive() -> None:
    assert map_coco("Person") is ObjectClass.PERSON
    assert map_coco("  PERSON  ") is ObjectClass.PERSON
    assert height_m("  TRUCK ", ObjectClass.CAR) == HEIGHT_M_BY_COCO["truck"]


def test_keep_list_only_emits_contract_classes() -> None:
    assert all(isinstance(v, ObjectClass) for v in COCO_TO_CLASS.values())


def test_every_height_override_names_a_mapped_coco_class() -> None:
    """An override for a name we never emit is dead code and a trap."""
    assert set(HEIGHT_M_BY_COCO) <= set(COCO_TO_CLASS)


@pytest.mark.parametrize("table", [COCO_TO_CLASS, HEIGHT_M_BY_CLASS, HEIGHT_M_BY_COCO])
def test_tables_are_immutable(table: object) -> None:
    with pytest.raises(TypeError):
        table["person"] = None  # type: ignore[index]
