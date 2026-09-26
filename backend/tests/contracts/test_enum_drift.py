"""Enum + numeric-bound drift tests.

The schema file is the only place a bound or an enum list may be authored.
Every constant the Python model enforces is asserted equal to it here so a
misspelled or renamed value fails loudly instead of silently diverging.
"""

from jsonschema import Draft202012Validator

from cane.contracts.awareness_event import (
    BEARING_DEG_MAX,
    BEARING_DEG_MIN_EXCLUSIVE,
    CLOSING_MPS_MAX,
    CLOSING_MPS_MIN,
    CONFIDENCE_MAX,
    CONFIDENCE_MIN,
    RANGE_M_MAX,
    RANGE_M_MIN,
    T_CAPTURE_MS_MAX,
    T_CAPTURE_MS_MIN,
    TRACK_ID_MAX_LENGTH,
    TRACK_ID_MIN_LENGTH,
    TTL_MS_MAX,
    TTL_MS_MIN,
    ObjectClass,
    RangeBand,
    Source,
)


def test_schema_itself_is_valid_draft_2020_12(schema):
    # A misspelled JSON Schema keyword is silently ignored otherwise.
    Draft202012Validator.check_schema(schema)


def test_object_class_matches_schema_enum(schema):
    assert [c.value for c in ObjectClass] == schema["properties"]["cls"]["enum"]


def test_source_matches_schema_enum(schema):
    assert [s.value for s in Source] == schema["properties"]["source"]["enum"]


def test_range_band_matches_schema_enum_minus_null(schema):
    schema_values = [v for v in schema["properties"]["range_band"]["enum"] if v is not None]
    assert [b.value for b in RangeBand] == schema_values


def test_track_id_length_bounds_match_schema(schema):
    prop = schema["properties"]["track_id"]
    assert TRACK_ID_MIN_LENGTH == prop["minLength"]
    assert TRACK_ID_MAX_LENGTH == prop["maxLength"]


def test_t_capture_ms_bounds_match_schema(schema):
    prop = schema["properties"]["t_capture_ms"]
    assert T_CAPTURE_MS_MIN == prop["minimum"]
    assert T_CAPTURE_MS_MAX == prop["maximum"]


def test_ttl_ms_bounds_match_schema(schema):
    prop = schema["properties"]["ttl_ms"]
    assert TTL_MS_MIN == prop["minimum"]
    assert TTL_MS_MAX == prop["maximum"]


def test_confidence_bounds_match_schema(schema):
    prop = schema["properties"]["confidence"]
    assert CONFIDENCE_MIN == prop["minimum"]
    assert CONFIDENCE_MAX == prop["maximum"]


def test_bearing_deg_bounds_match_schema(schema):
    prop = schema["properties"]["bearing_deg"]
    assert BEARING_DEG_MIN_EXCLUSIVE == prop["exclusiveMinimum"]
    assert BEARING_DEG_MAX == prop["maximum"]


def test_range_m_bounds_match_schema(schema):
    prop = schema["properties"]["range_m"]
    assert RANGE_M_MIN == prop["minimum"]
    assert RANGE_M_MAX == prop["maximum"]


def test_closing_mps_bounds_match_schema(schema):
    prop = schema["properties"]["closing_mps"]
    assert CLOSING_MPS_MIN == prop["minimum"]
    assert CLOSING_MPS_MAX == prop["maximum"]
