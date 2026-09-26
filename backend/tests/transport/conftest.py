"""Shared fixtures for the transport test suite.

Resolves the repo root from ``__file__`` exactly once, mirroring
``tests/contracts/conftest.py``, so envelope-schema and golden-fixture
paths are not repeated in every test file.
"""

import json
from pathlib import Path
from typing import Any

import pytest
from jsonschema import Draft202012Validator
from referencing import Registry, Resource
from referencing.jsonschema import DRAFT202012

from cane.contracts.awareness_event import AwarenessEvent, parse_awareness_event

REPO_ROOT = Path(__file__).resolve().parents[3]
EVENT_SCHEMA_PATH = REPO_ROOT / "contracts" / "awareness_event.schema.json"
ENVELOPE_SCHEMA_PATH = REPO_ROOT / "contracts" / "transport_envelope.schema.json"
FIXTURE_PATH = REPO_ROOT / "contracts" / "fixtures" / "awareness_events.golden.json"


@pytest.fixture(scope="session")
def golden_valid_event() -> AwarenessEvent:
    """The fixture's first ``expect: valid`` case
    (``phone_metric_both_range_fields``, per task-3-brief.md G), parsed
    into a real :class:`AwarenessEvent`.
    """
    with FIXTURE_PATH.open("r", encoding="utf-8") as f:
        data: dict[str, Any] = json.load(f)
    first_valid = next(case for case in data["cases"] if case["expect"] == "valid")
    return parse_awareness_event(first_valid["event"])


@pytest.fixture(scope="session")
def envelope_validator() -> Draft202012Validator:
    """A validator for ``transport_envelope.schema.json`` with the
    ``awareness_event.schema.json`` `$ref` resolved through a registry
    (both schemas' real ``$id``\\ s), so ``event`` frames validate their
    nested payload too.
    """
    with ENVELOPE_SCHEMA_PATH.open("r", encoding="utf-8") as f:
        envelope_schema = json.load(f)
    with EVENT_SCHEMA_PATH.open("r", encoding="utf-8") as f:
        event_schema = json.load(f)

    registry: Registry = Registry().with_resource(
        event_schema["$id"],
        Resource.from_contents(event_schema, default_specification=DRAFT202012),
    )
    return Draft202012Validator(envelope_schema, registry=registry)
