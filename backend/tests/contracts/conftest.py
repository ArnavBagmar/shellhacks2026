"""Shared pytest fixtures for the contract test suite.

Resolves the repo root from ``__file__`` exactly once so no other test file
needs to know how many directories separate it from ``contracts/``.
"""

import json
from pathlib import Path
from typing import Any

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
SCHEMA_PATH = REPO_ROOT / "contracts" / "awareness_event.schema.json"
FIXTURE_PATH = REPO_ROOT / "contracts" / "fixtures" / "awareness_events.golden.json"


@pytest.fixture(scope="session")
def schema() -> dict[str, Any]:
    with SCHEMA_PATH.open("r", encoding="utf-8") as f:
        return json.load(f)


@pytest.fixture(scope="session")
def fixture() -> dict[str, Any]:
    with FIXTURE_PATH.open("r", encoding="utf-8") as f:
        return json.load(f)


def pytest_generate_tests(metafunc: pytest.Metafunc) -> None:
    """Parametrize tests that ask for a ``case`` or ``malformed_payload``
    argument directly from the golden fixture, so the fixture path is
    resolved in exactly the one place above.
    """
    if "case" in metafunc.fixturenames:
        with FIXTURE_PATH.open("r", encoding="utf-8") as f:
            data = json.load(f)
        cases = data["cases"]
        metafunc.parametrize("case", cases, ids=[c["name"] for c in cases])
    if "malformed_payload" in metafunc.fixturenames:
        with FIXTURE_PATH.open("r", encoding="utf-8") as f:
            data = json.load(f)
        payloads = data["malformed_payloads"]
        metafunc.parametrize(
            "malformed_payload",
            payloads,
            ids=[f"malformed_{i}" for i in range(len(payloads))],
        )
