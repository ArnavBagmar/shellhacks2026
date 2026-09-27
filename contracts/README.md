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

`transport_envelope.schema.json` (draft 2020-12) documents the eight
WebSocket frame shapes the laptop (server) and phone (client) exchange —
see `docs/camera-sync-design.md` §4.2 and §4.4 and `cane.transport.messages`
/ `CaneTransport`'s `Envelope.swift` for the code. Every frame is a JSON text
object with a `type` string:

```json
{"type": "ping", "t1": 1790000000000}
{"type": "pong", "t1": 1790000000000, "t2": 1790000000012, "t3": 1790000000013}
{"type": "event", "event": { "...": "the 11-field AwarenessEvent, untouched" }}
{"type": "health", "t_phone_ms": 1790000000000, "window_ms": 10000,
 "received": 27, "stale_dropped": 7, "stale_drop_rate": 0.26,
 "complete_buckets": 10}
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
- `health` is the phone's stale-drop signal (§4.4), sent phone → laptop.
  It carries one report of the feedback manager's ring of ten one-second
  `{received, staleDropped}` buckets: `stale_drop_rate` at or above **0.2**
  over at least **2** complete buckets is the §3.4 trigger that flips the
  laptop to its local detector. `t_phone_ms` is PHONE-clock time like every
  other phone-produced stamp here; the laptop converts before judging
  freshness and **ignores a report older than 3 s**, so a signal that has
  died cannot drive a switch. The frame is advisory: the health monitor
  also steps rungs and flips on its own RTT and timeout evidence, and a
  phone that never sends `health` changes nothing.
- `guide` (phone → laptop, ~8 Hz) is "guide my hand" live: the tracked
  object's `box` and the nearest fingertip (`hand`) as frame fractions, the
  fingertip-to-surface `gap_m`, whether TrueDepth measured it (`metric`), the
  last direction word (`hint`), and a small `frame` (the `phone_frame` shape)
  a few times a second. Display-only: the laptop page draws it and nothing
  acts on it; see `docs/hand-guide.md`.
- The type `location` is **reserved** (phone → laptop, defined in a later
  step). A receiver that sees an unrecognised `type` logs at debug level
  and ignores the frame — never errors, never disconnects. Malformed JSON,
  an `event` payload that fails contract validation, or a `health` frame
  with a missing, out-of-range or unknown field, is logged (warning) and
  dropped; the connection stays up.
- One event per frame. Batching (`{"events": [...]}`) is a future envelope
  decision, not part of this schema.
- `query` (phone → laptop) and `answer` (laptop → phone) are step 20's
  spoken questions ("Hey Cane, is there a bathroom around?", see
  `docs/steps/step-20-brief.md`). One `answer` per `query_id`; a newer
  query supersedes an older one. `kind` is `find`, `describe_around`,
  `describe_front`, or `ask` (any other question, answered in a sentence).
  `speak` (phone → laptop) / `voice` (laptop → phone) carry the natural
  voice: one `voice` per `speak_id`, `ok` with base64 audio + its format, or
  `unavailable` with neither (the phone then uses its own voice). Hazards
  are never sent as `speak`.
  `target` is required exactly when `kind` is `find`; `bearing_deg` is present exactly when `status` is `found`
  (same `(-180, 180]` torso-relative convention as events). The optional
  `phone_frame` is one base64 front-camera JPEG, unmirrored. As everywhere
  else, every key is present and unknown keys are rejected -- with one
  exception: `answer.phone_box` {`x_min`, `y_min`, `x_max`, `y_max`}, fractions
  of the phone photo from its top-left corner, is sent only on a `found`
  answer located in that photo and is *omitted* otherwise (phones built
  before it reject unknown keys). The phone measures TrueDepth distance
  inside it. `x_min < x_max`, `y_min < y_max` is checked by both decoders
  (the schema cannot say it):

  ```json
  {"type": "query", "query_id": "q-7f3a", "kind": "find", "target": "bathroom",
   "utterance": "is there a bathroom around", "t_phone_ms": 1790000000000, "phone_frame": null}
  {"type": "answer", "query_id": "q-7f3a", "status": "found", "subject": "restroom sign",
   "bearing_deg": 72.5, "detail": "past the elevators", "confidence": "medium", "latency_ms": 2140}
  ```

  `fixtures/transport_query_answer.golden.json` holds valid and invalid
  frames; the Python decoder, the Swift decoder and the schema itself are
  all tested against it.
- `steer` (laptop → phone) is step 21's steering command, one per sensing
  tick (`docs/shepherd-steering-plan.md`). It is the pipeline's most
  perishable frame: `ttl_ms` is always **400** (`STEER_TTL_MS`), and once
  it expires the phone decays its cue to neutral over 500 ms instead of
  holding the last lean. In local-fallback mode the laptop sends **no**
  `steer` at all — a zero steer would be a silent fake of having looked.

  ```json
  {"type": "steer", "t_capture_ms": 1790000000000, "ttl_ms": 400, "steer": 0.42,
   "proximity": 0.61, "gap_bearing_deg": 37.5, "blocked": false,
   "proximity_anchored": true, "source": "x4_cloud"}
  ```

  `steer` is −1…+1 with **positive = right**; `proximity` is 0 (clear) … 1
  (at the stopping distance); `gap_bearing_deg` uses the same half-open
  `(-180, 180]` torso-relative convention as events, so exactly `-180.0`
  is rejected here too. Two fields exist so the phone never has to guess:
  `proximity_anchored` is false when the ground-plane scale fit was
  rejected, and proximity is then a band rather than a number; `blocked`
  means no gap was wide enough to walk through, which is *not* the same as
  `steer == 0.0` (a clear corridor also steers 0.0).
  `fixtures/transport_steer.golden.json` holds valid and invalid frames and
  is run by the Python decoder, the Swift decoder and the schema.

Both language test suites do not validate against
`transport_envelope.schema.json` directly; it is documentation, checked by
two Python unit tests — `backend/tests/transport/test_messages.py`, that
every message `cane.transport.messages.encode` produces validates against
it, and `backend/tests/transport/test_health_message.py`, that the
`health` definition's own `examples` validate and decode.
