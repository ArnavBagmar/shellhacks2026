"""Golden fixture tests.

Every case in ``contracts/fixtures/awareness_events.golden.json`` runs
through both jsonschema and the Pydantic model; a disagreement between the
two validators fails loudly. Case parametrization happens in conftest.py's
``pytest_generate_tests`` so the fixture path is resolved in exactly one
place.
"""

import json
import re

import pytest
from jsonschema import Draft202012Validator

from cane.contracts.awareness_event import AwarenessEventError, parse_awareness_event

MIN_VALID_CASES = 8
MIN_INVALID_CASES = 10

_README_TEMPLATE_HEADING = "## Copy-paste event template"


def test_fixture_envelope_is_sane(fixture):
    assert fixture["fixture_version"] == 1
    cases = fixture["cases"]

    names = [c["name"] for c in cases]
    assert len(names) == len(set(names)), "case names must be unique"

    for case in cases:
        assert case["expect"] in ("valid", "invalid")
        if case["expect"] == "invalid":
            assert "reason" in case, f"{case['name']}: invalid case must carry a reason"
        else:
            assert "reason" not in case, f"{case['name']}: valid case must not carry a reason"

    valid_count = sum(1 for c in cases if c["expect"] == "valid")
    invalid_count = sum(1 for c in cases if c["expect"] == "invalid")
    assert valid_count >= MIN_VALID_CASES
    assert invalid_count >= MIN_INVALID_CASES


def test_fixture_case_matches_both_validators(case, schema):
    expect_valid = case["expect"] == "valid"
    event = case["event"]

    schema_says_valid = Draft202012Validator(schema).is_valid(event)
    assert schema_says_valid == expect_valid, (
        f"{case['name']}: jsonschema validity ({schema_says_valid}) "
        f"disagrees with fixture expectation ({expect_valid})"
    )

    try:
        parse_awareness_event(event)
        pydantic_says_valid = True
    except AwarenessEventError:
        pydantic_says_valid = False
    assert pydantic_says_valid == expect_valid, (
        f"{case['name']}: Pydantic validity ({pydantic_says_valid}) "
        f"disagrees with fixture expectation ({expect_valid})"
    )


def test_malformed_payload_is_rejected_not_crashed(malformed_payload):
    with pytest.raises(AwarenessEventError):
        parse_awareness_event(malformed_payload)


def _extract_readme_template(readme_text: str) -> dict:
    heading_index = readme_text.index(_README_TEMPLATE_HEADING)
    match = re.search(r"```json\s*\n(.*?)\n```", readme_text[heading_index:], re.DOTALL)
    assert match, f"contracts/README.md: no ```json block under '{_README_TEMPLATE_HEADING}'"
    return json.loads(match.group(1))


def test_readme_template_is_schema_valid(readme_text, schema):
    """A developer who copies contracts/README.md's template verbatim must
    get a valid event, not an immediately rejected one."""
    template = _extract_readme_template(readme_text)

    errors = sorted(Draft202012Validator(schema).iter_errors(template), key=str)
    assert not errors, [e.message for e in errors]

    parse_awareness_event(template)  # must not raise AwarenessEventError
