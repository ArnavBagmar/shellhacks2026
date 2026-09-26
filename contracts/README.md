# contracts/

Two shared artifacts that decouple the three lanes (iOS perception, Python X4
bridge/cloud, iOS feedback manager). Nothing here is transport or detector
logic — see `docs/camera-sync-design.md` §4.6–§4.8 for that.

- `awareness_event.schema.json` — JSON Schema (draft 2020-12), the single
  source of truth for the 11-field wire format, including the closed `cls`
  enum and every numeric bound. Both languages' models are asserted equal to
  it; a bound may only be authored here.
- `fixtures/awareness_events.golden.json` — golden fixture: valid and invalid
  `AwarenessEvent` cases plus a `malformed_payloads` array of raw strings.
  This is the one test that catches cross-language drift.

## Running the fixture

- Python: `cd backend && uv run pytest`
- Swift: `cd ios/CaneKit && swift test`

Both suites load the exact same fixture file; do not fork it per language.
The fixture must contain at least one `invalid` case per constraint keyword
in the schema (`enum`, `const`, `minimum`, `maximum`, `exclusiveMinimum`,
`minLength`, `maxLength`, `additionalProperties`, the `if`/`else`
conditional). A `reason` on an invalid case is documentation only — never
assert against it in either test suite.

## Field semantics not obvious from the schema

1. **`range_band`**: `near` <= 5 m, `mid` 5–15 m, `far` > 15 m. Producers pick
   their own box-height thresholds to hit these bands; consumers may rely on
   these numbers.
2. **`closing_mps`**: line-of-sight range rate relative to the wearer,
   positive = closing. Not the object's ground speed — a cyclist at 6.7 m/s
   approaching a wearer walking 1.4 m/s toward them is `closing_mps ~= 8.1`.
3. **`bearing_deg`**: half-open interval `(-180, 180]`. `atan2` returns
   exactly `-180` for directly behind, so a naive radians-to-degrees
   conversion can emit `-180.0` and be rejected. Producers must normalize
   `bearing_deg <= -180` by adding 360 before emitting.
4. **`track_id`**: stable across frames for the same physical object, unique
   only *within* a `source`. The consumer's refractory key is the pair
   `(source, track_id)`, never `track_id` alone. A producer with no tracker
   (the phone) must still synthesize a key stable frame-to-frame — e.g. `cls`
   plus a quantized bearing bucket.
5. **`cls`**: closed at 12 values. A detector class outside the 12 must be
   mapped by the producer, never emitted as a new string:

   | detector class | wire `cls` |
   |---|---|
   | bicycle | `cyclist` |
   | motorcycle | `scooter` |
   | truck | `car` |
   | anything else | `unknown_obstacle` |

## Copy-paste event template (D2 field order)

```json
{
  "schema": 1,
  "source": "phone",
  "track_id": "example-001",
  "t_capture_ms": 1790000000000,
  "ttl_ms": 1,
  "cls": "unknown_obstacle",
  "confidence": 0.0,
  "bearing_deg": 0.0,
  "range_m": null,
  "range_band": null,
  "closing_mps": null
}
```

This template is itself schema-valid (see
`backend/tests/contracts/test_golden_fixture.py::test_readme_template_is_schema_valid`)
— copy it verbatim and only change the values, not the shape.

`contracts/` is frozen after Step 2 — changes need controller approval.
A `schema` version bump is a simultaneous redeploy of cloud, bridge and a
rebuilt iOS app; treat it as re-running this step, not a patch.

## Transport envelope

`transport_envelope.schema.json` (draft 2020-12) documents the three
WebSocket frame shapes the laptop (server) and phone (client) exchange —
see `docs/camera-sync-design.md` §4.2 and `cane.transport.messages` /
`CaneTransport`'s `Envelope.swift` for the code. Every frame is a JSON text
object with a `type` string:

```json
{"type": "ping", "t1": 1790000000000}
{"type": "pong", "t1": 1790000000000, "t2": 1790000000012, "t3": 1790000000013}
{"type": "event", "event": { "...": "the 11-field AwarenessEvent, untouched" }}
```

- The laptop initiates every `ping`; the phone only answers with `pong`,
  echoing `t1` so the laptop can match it, with `t2` = phone receive time
  and `t3` = phone send time. All three timestamps are integer
  Unix-epoch milliseconds.
- `event` carries exactly one `AwarenessEvent`, nested untouched under the
  `"event"` key — the envelope never adds a key to it, and it is never
  validated against `awareness_event.schema.json` directly (only the
  nested `event` value is, via the `$ref` in
  `transport_envelope.schema.json`).
- The types `health` and `location` are **reserved** (phone → laptop,
  defined in later steps). A receiver that sees an unrecognised `type`
  logs at debug level and ignores the frame — never errors, never
  disconnects. Malformed JSON, or an `event` payload that fails contract
  validation, is logged (warning) and dropped; the connection stays up.
- One event per frame. Batching (`{"events": [...]}`) is a future envelope
  decision, not part of this schema.

Both language test suites do not validate against
`transport_envelope.schema.json` directly; it is documentation, checked by
one Python unit test (`backend/tests/transport/test_messages.py`) that
every message `cane.transport.messages.encode` produces validates against
it.
