# Task 2 design review — AwarenessEvent contract (ecc:architect, opus)

Controller ruling: **ACCEPTED, all seven amendments are binding.** Where an amendment conflicts with section D of task-2-brief.md, the amendment wins. `ttl_ms` maximum = 15000 (the architect's recommended value). The `cls` enum stays at 12 values with the producer-side mapping table (hazard 5).

## 1. Verdict

ACCEPT WITH AMENDMENTS. Section D is structurally right. The three load-bearing calls — `contracts/` at the repo root as the single shared file, all 11 keys required with explicit nulls, and no `urgency` on the wire — are correct and ratified. The amendments are all of one kind: D specifies the shape well but under-specifies the places where the two languages can silently disagree, and under-bounds the two fields whose corruption fails silently rather than loudly (`t_capture_ms`, which gates the entire §4.4 safety property, and the unbounded floats that feed TTC). None changes the field list.

The fixture is not really a test; it is the specification of the wire in executable form. Everywhere D leaves a bound or a convention stated only in prose, the fixture and the drift tests cannot enforce it.

## 2. Amendments (paste-ready, binding)

### A1 — `t_capture_ms` plausibility window and the future-dating rule

Replace D2 bullet 4 with:

> 4. `t_capture_ms` — integer, `minimum: 1600000000000`, `maximum: 4000000000000` (phone-clock Unix epoch **milliseconds**, D-corrected). The window is deliberately narrow: it rejects a seconds-valued timestamp (`1790000000` is below the minimum) and a microsecond-valued one (`1790000000000000` is above the maximum). `description`: "Phone-clock Unix epoch milliseconds, D-corrected per camera-sync-design §4.3. Seconds or microseconds here are a bug, not a unit choice."

Add to D2 after the conditional rule:

> Consumers additionally treat `t_capture_ms > now_phone_ms + 250` as **invalid** (not stale) and count it on a *separate* counter that does **not** feed the §3.4 fallback trigger. A future-dated event means the NTP offset or D is wrong, which makes every event look permanently fresh and silently switches the staleness gate off; flipping to the local detector cannot fix a clock error. This is a step-9 constant, not a schema keyword — recorded here so step 9 does not have to rediscover it.

Add to the D3 case list: `invalid t_capture_ms 1790000000 (seconds, not ms)`; `invalid t_capture_ms -1`.

### A2 — complete the numeric bounds; drift tests cover bounds, not only enums

Replace D2 bullets 3, 5, 7, 9, 11:

> 3. `track_id` — string, `minLength: 1`, `maxLength: 64`.
> 5. `ttl_ms` — integer, `minimum: 1`, `maximum: 15000` (the largest value in the §4.4 table).
> 7. `confidence` — number, `minimum: 0`, `maximum: 1`.
> 9. `range_m` — `["number","null"]`, `minimum: 0`, `maximum: 50`. `description`: "Metres. Phone only, and only while §2.2 scale anchoring is healthy. Trustworthy to ~15 m (§4.5); the 50 m ceiling exists to catch a diverged scale factor, not to express policy."
> 11. `closing_mps` — `["number","null"]`, `minimum: -60`, `maximum: 60` (+ = approaching, negative = receding).
>
> No field may carry `NaN` or `±Infinity`. JSON literals for these are not valid JSON, but Python's `json.loads` accepts them as a non-standard extension, so `parse_awareness_event` must pass a `parse_constant` hook that raises `AwarenessEventError`, and the Pydantic float fields must set `allow_inf_nan=False`. Do **not** put a NaN case in the golden fixture — it would make the fixture file itself unparseable by `JSONSerialization` on the Swift side.

Replace the D6 drift-test bullets with:

> - Python `test_enum_drift.py` and Swift `EnumDriftTests` both compare (a) the three enum value lists against the schema's `enum` arrays, and (b) **every numeric bound in the schema** (`t_capture_ms.minimum/maximum`, `ttl_ms.minimum/maximum`, `confidence.minimum/maximum`, `bearing_deg.exclusiveMinimum/maximum`, `range_m.minimum/maximum`, `closing_mps.minimum/maximum`, `track_id.minLength/maxLength`) against the constant the model actually enforces. The schema file is the only place a bound may be authored; the model constants are asserted equal to it.
> - One Python test calls `jsonschema.Draft202012Validator.check_schema(schema)`. A misspelled keyword in JSON Schema is silently ignored.
> - The golden fixture must contain at least one `invalid` case per constraint keyword in the schema. `contracts/README.md` states this rule.

### A3 — the exact conditional form

Replace the D2 conditional bullet with:

> Conditional rule (X4 never states metres) — express it in exactly this form, which applies to **both** `x4_cloud` and `x4_local`:
> ```json
> "if":   { "properties": { "source": { "const": "phone" } }, "required": ["source"] },
> "else": { "properties": { "range_m": { "type": "null" } } }
> ```
> The `required: ["source"]` is not decoration: a `properties`-only `if` subschema is vacuously true when the key is absent. Use the positive `const` + `else` form rather than `not: {const: "phone"}`. The `if`/`else` subschemas may only constrain keys already declared in the top-level `properties` — `additionalProperties: false` does not see keys introduced inside a subschema.
>
> The Pydantic mirror is a `@model_validator(mode="after")` that raises `ValueError` (never `assert`) when `source is not Source.PHONE and range_m is not None`; the Swift mirror enforces the same predicate inside `init(from:)`. The three validators are **not** required to agree on *which* error they report for an event that violates several rules at once. The fixture's `reason` field is documentation and must never be asserted against in either test suite.

Add to the D3 case list: `invalid x4_local with range_m 4.2`.

### A4 — five semantics that must live in the schema `description` fields (and in contracts/README.md)

> 1. `range_band`: `near` ≤ 5 m, `mid` 5–15 m, `far` > 15 m. Producers pick their own box-height thresholds to hit these; consumers may rely on these numbers.
> 2. `closing_mps`: **line-of-sight range rate relative to the wearer**, positive = closing. Not the object's ground speed. A cyclist at 6.7 m/s approaching a wearer walking 1.4 m/s toward them is `closing_mps ≈ 8.1`.
> 3. `bearing_deg`: the interval is half-open, `(-180, 180]`. `atan2` returns exactly `-π` for directly behind, so a producer converting radians to degrees can emit exactly `-180.0` and be rejected. **Producers must normalize `bearing_deg <= -180` by adding 360 before emitting.** Untreated, this silently drops the rear bearing — the X4's highest-value sector.
> 4. `track_id`: stable across frames for the same physical object; unique only **within** a `source`. The consumer's refractory key is the pair `(source, track_id)`, never `track_id` alone. A producer with no tracker (the phone) must still synthesize a key that is stable frame-to-frame for the same object — e.g. `cls` plus a quantized bearing bucket — because gate 4 is the only anti-flooding mechanism.
> 5. `cls`: the enum is closed at 12 values. A detector class outside the 12 must be **mapped by the producer**, never emitted as a new string: `bicycle → cyclist`, `motorcycle → scooter`, `truck → car`, anything else → `unknown_obstacle`. The mapping table lives in `contracts/README.md`.

### A5 — Python: pin observable behaviour

Replace the first and third D4 bullets with:

> - `class AwarenessEvent(BaseModel)` with `model_config = ConfigDict(extra="forbid", frozen=True, strict=True)`. The wire key for the version field is `schema`; Pydantic v2 may refuse a field literally named `schema` because it shadows a `BaseModel` attribute. Either name works — what is required and tested is the observable: a round-trip test asserts that serializing an event produces a JSON object with **exactly the 11 wire keys**, that the version key is spelled `schema`, and that a payload using any other spelling is rejected as an extra key. If an alias is used, serialization must go through `by_alias=True` and validation must accept the alias (`populate_by_name` as needed).
> - Serialization must **never** use `exclude_none` (and no FastAPI route may set `response_model_exclude_none`). All 11 keys are required on the wire, including explicit nulls.
> - Numeric typing must be strict enough that `{"ttl_ms": "500"}` is **rejected**. Pydantic's lax mode coerces `"500" → 500` while `jsonschema` rejects it, so a lax model makes the two validators disagree on live data.
> - `parse_awareness_event` passes a `parse_constant` hook to `json.loads` that raises, so `NaN`/`Infinity`/`-Infinity` become `AwarenessEventError`.

### A6 — Swift: custom encoder; one shared fixture-path helper

Add to D5 and D6:

> - `encode(to:)` must be written by hand and must use `container.encode(rangeM, forKey:)` for the three nullable fields — **not** `encodeIfPresent`. The synthesized `Encodable` omits nil keys, so it would emit 8 keys and `AwarenessEvent.decode` would reject its own output. The round-trip test asserts both that decode(encode(e)) == e **and** that the encoded JSON object has exactly 11 keys.
> - Both Swift test files resolve the fixture and schema through a single shared helper (`ContractPaths.swift` in the test target) so the `../../../../` literal exists exactly once. Use `#filePath`, never `#file`; on failure throw a message containing the absolute path computed.
> - Add one test that a payload with explicit `"range_m": null` decodes, and one that a payload with `range_m` **absent** throws.
> - Python mirror: `backend/tests/contracts/conftest.py` resolves the repo root from `__file__` once and exposes `schema` and `fixture` pytest fixtures.

### A7 — fixture envelope and coverage

Replace D3 with the same format plus:

> - The envelope's version key is `fixture_version` (value `1`), not `schema`.
> - Add a second top-level array, `"malformed_payloads": ["", "{", "null", "[]", "\"x\"", "42", "{\"schema\":1"]` — **raw strings**, each of which both `parse_awareness_event` and `AwarenessEvent.decode` must reject as their declared error type without crashing.
> - Both suites assert on the envelope before running cases: `expect` is exactly `"valid"` or `"invalid"`, case `name`s are unique, and the case count is at or above the enumerated minimum.
> - `reason` is required on every `invalid` case and absent on valid ones. Valid cases may carry an optional `note` (e.g. `"+42.0 means 42 degrees to the wearer's right"`).
> - Number formatting: every `number`-typed field is written with a decimal point (`180.0`, `0.0`, `1.0`) and every `integer`-typed field without one. Do **not** add a case testing an integral float in an integer field.
> - Keys inside each `event` appear in the D2 field order; cases are grouped valid-first then invalid. No `base` event plus patches. `contracts/README.md` carries a copy-paste 11-key template.
>
> **Additional required cases:** valid **phone event with non-null `closing_mps` and `range_m` set** (the only combination from which urgency 3 can be computed); valid `x4_cloud` existence-only (both range fields null); invalid **missing `closing_mps`**; invalid **missing `range_band`**; invalid missing `t_capture_ms`; invalid `{"ttl_ms": "500"}`; invalid `range_m: -0.5`; invalid `track_id` of 65+ characters; plus the A1 and A2 boundary cases (`ttl_ms 15001`, `range_m 50.5`, `closing_mps 61.0`, `t_capture_ms 1790000000`, `t_capture_ms -1`, `x4_local range_m 4.2`).

## 3. Answers to the controller's questions (summary)

Q1 all-11-keys-required: keep. Q2 conditional: right rule, A3 pins the sound form. Q3 bounds: incomplete → A1/A2; the dedup rule "phone with healthy metric range inside 15 m" is derivable as `source == phone && range_m != nil && range_m <= 15` — nobody should ask for an `anchor_healthy` field. Q4: allow `closing_mps` on phone events (TTC needs both `range_m` and `closing_mps`, and only the phone may populate `range_m` — otherwise urgency 3 is unreachable); no `track_id` format, refractory key = `(source, track_id)`. Q5 fixture: A7. Q6 layout: `contracts/` at root is right; `#filePath` approach right with A6 hardening; after step 2, `contracts/` is frozen behind controller approval; P1 and P3 add **targets to `ios/CaneKit`**, not sibling packages; keep `Package.swift` lists one-entry-per-line. Q7: `schema` bump = simultaneous redeploy of cloud, bridge and a rebuilt iOS app — treat as re-running step 2, not a patch.

## 4. Hazards for downstream steps (controller notes, not step-2 work)

1. Step 3 must not touch this schema; the envelope is a separate schema; never add a key to the event object.
2. Step 9 owns two counters: `stale_drops` (feeds §3.4 fallback) and `future_dated` (does not).
3. Step 9 clamps `ttl_ms` to the §4.4 table for `cls` (`min(ttl_ms, table[cls])`); the TTL helper in step 9 is the only code that writes `ttl_ms`.
4. Refractory state keyed `(source, track_id)`; expect a reset on cloud↔local failover — add a short suppression window on failover.
5. `cls` enum coverage gaps (truck, motorcycle, train, dog) are handled by the A4.5 producer mapping; keep 12.
6. Intra-source seam duplicates (two X4 crops seeing one object near ±90°) are not covered by §4.7's cross-source dedup — step 9 concern.
7. `$id` is a non-resolvable URI; keep the schema `$ref`-free.
8. `allow_inf_nan=False` / strict mode must not be reverted by a later FastAPI refactor; no `response_model_exclude_none`.
