# Task 2 brief — Event contract + golden fixtures

Extracted from docs/orchestration-runbook.md (Step 2) and docs/camera-sync-design.md §4.4–§4.8, plus controller rulings. This file is the single source of requirements; exact values here are binding.

## A. Runbook step text (verbatim)

## Step 2 — Event contract + golden fixtures

**Intent:** Build the two shared artifacts that decouple all three lanes. Everything else is
independent of everything else through this file.
**Chain rationale:** `architect` owns the schema shape since it is the one decision that is
expensive to change at hour 30; `tdd-guide` writes the fixture suite first; both language
reviewers close because the contract exists in Python and Swift simultaneously.

```bash
/everything-claude-code:orchestrate custom "ecc:architect,ecc:tdd-guide,ecc:python-reviewer,ecc:swift-reviewer" "[Plan: docs/camera-sync-design.md#step-2] Build the integration surface: contracts/awareness_event.schema.json with the 11 fields of §4.6, the closed class enum in one shared file, a Pydantic model for the backend, a Codable struct for Swift, and a golden fixture of ~10 valid and invalid events both test suites run against. urgency is NOT on the wire; range_m is never populated by the X4. Acceptance: both suites pass the same fixture; an invalid event is rejected rather than crashed on; a schema version field is present. Out of scope: any detector or feedback logic"
```

---


## B. Spec: camera-sync-design.md §4.4–§4.8 (verbatim)

### 4.4 Staleness policy

`t_capture` exists for one purpose: deciding whether a warning is still true. Objects have
different half-lives, so one global timeout is wrong.

| class | TTL | reasoning |
|---|---|---|
| cyclist / scooter | 500 ms | 6.7 m/s → 3.4 m of closure; past that the bearing is a lie |
| moving car | 400 ms | faster closure |
| pedestrian | 1.5 s | ~1.4 m/s |
| pole, bollard, wall, parked_car, door, overhang | 8 s | it is not moving — only you are |
| scene description | 15 s | latency-tolerant by nature |

**The feedback manager drops. It never announces late.** Silence is a valid output — design
principle #2, with teeth.

Then reuse the signal: **count the drops.** A rising stale-drop rate is the cleanest trigger for
the §3.4 fallback — one mechanism, two jobs, no separate health probe.

### 4.5 Sector ownership

The partition is **angular × range**, not purely angular. The phone's monocular range stays
trustworthy to roughly 15 m; past that, boxes get small and depth gets soft.

```
bearing:  0°        30° 37°         90°               150°    180°
          │          │   │           │                 │       │
 PHONE    ██████████████                                          ±37°, to ~15 m
 X4              █████████████████████████████████████████████    ±30° → ±180°
          └─ phone ──┘└ ov ┘└────────── X4 exclusive ─────────┘
              only    30-37°
```

| sector | range | owner | why |
|---|---|---|---|
| front, \|θ\| ≤ 37° | ≤ 15 m | **phone** | on-device, <100 ms, metric — the collision zone |
| front, \|θ\| ≤ 37° | > 15 m | **X4** | existence only, no range |
| \|θ\| > 30° | any | **X4** | the phone physically cannot see it |

The 30–37° overlap exists so Δ calibration error cannot open a gap.

### 4.6 The event contract

Eleven fields. All three lanes code against this.

```python
@dataclass(frozen=True)
class AwarenessEvent:
    schema: int                # 1 — bump on any breaking change
    source: str                # "phone" | "x4_cloud" | "x4_local"
    track_id: str              # stable per object; the anti-flooding key
    t_capture_ms: int          # PHONE CLOCK, D-corrected. The canonical time.
    ttl_ms: int                # from the §4.4 table
    cls: str                   # closed enum below
    confidence: float          # 0..1
    bearing_deg: float         # torso-relative, 0 = ahead, + = right
    range_m: float | None      # phone only, and only while scale anchoring is healthy
    range_band: str | None     # "near" | "mid" | "far" — X4 always; phone when unanchored
    closing_mps: float | None  # + = approaching
```

**Two deliberate omissions, both load-bearing.**

`urgency` is **not** on the wire, though the original plan's format had it. **Producers emit facts;
the consumer computes urgency.** Urgency depends on user state — mid-crossing, stationary, already
warned — which only the phone knows. Bake it into the backend and you cannot change announcement
policy without a redeploy at hour 30.

`range_m` is **never** populated by the X4. It is monocular through a fisheye, with no usable
ground plane in a rear-facing crop; any number would be a guess dressed as a measurement. It emits
`range_band` from box height instead. The **phone** may state metres, but only while §2.2 scale
anchoring is healthy — if the anchor diverges, the phone drops to bands too.

Announcements round: **"about two metres," never "2.3 metres."** Urgency thresholds use bands with
hysteresis so a noisy estimate cannot flip urgency back and forth.

**Closed class enum** (both sides must agree, so it lives in one file):

```
moving:  person · cyclist · scooter · car · bus
static:  pole · bollard · wall · parked_car · door · overhang
catch:   unknown_obstacle
```

`unknown_obstacle` matters more than it looks: detectors find shapes they cannot label, and those
still need to reach the user.

### 4.7 Arbitration — the feedback manager pipeline

Every event runs this gauntlet in order:

1. **Validate** — schema version, enum membership, bearing in `(-180, 180]`, `ttl > 0`. Reject and
   log; never crash on a malformed event.
2. **Staleness gate** — `now − t_capture > ttl` → drop, increment the §3.4 counter.
3. **Dedup gate** — another event within 10° and 300 ms from a different source? Prefer the phone
   when it has healthy metric range inside 15 m; otherwise keep the X4's. Bearing + time
   proximity, not data association.
4. **Refractory suppression** — this `track_id` announced recently and urgency has not escalated?
   Drop. **This is the anti-flooding mechanism**, and it is why `track_id` is in the contract.
5. **Urgency computation** — on-device policy, below.
6. **Priority queue** — one announcement at a time; higher urgency preempts one in progress.

| urgency | trigger | output | refractory |
|---|---|---|---|
| **3 immediate** | time-to-contact < 2 s | haptic burst + one word ("Stop") | 0 — always speaks |
| **2 act soon** | TTC 2–5 s, or static obstacle < 2 m | directional haptic + 2–3 words | 2 s |
| **1 aware** | worth knowing, no action needed | **directional haptic only, no speech** | 8 s |
| **0 ambient** | — | log + hazard map, never announced | — |

`TTC = range_m / closing_mps`. Without metric range, `range_band` substitutes — `near` + fast
closing → urgency 2–3.

Urgency 1 being haptic-only is where "don't flood the ears" stops being a slide and becomes code.

### 4.8 Keeping two languages in sync

P1 and P3 write Swift, P2 writes Python, and at hour 20 they will disagree about a field
name. The fixture file is also how P3 builds the entire feedback manager on hour 1 with no
camera, no laptop and no cloud — see `device-responsibilities.md`.

- `contracts/awareness_event.schema.json` — single source of truth, in the repo
- Pydantic model on the backend (FastAPI already validates this way), `Codable` struct on Swift
- **A golden fixture file: ~10 events, valid and invalid, that both sides' test suites run against**

That fixture is the one test that catches cross-language drift, and it takes twenty minutes.


## C. Spec: build-plan.md closed class enum (verbatim)

**Closed class enum — both sides must agree, lives in one file:**

```
moving:  person · cyclist · scooter · car · bus
static:  pole · bollard · wall · parked_car · door · overhang
catch:   unknown_obstacle
```

`unknown_obstacle` matters more than it looks: detectors find shapes they cannot label, and those
still need to reach the user.

## D. Controller rulings — binding, resolve every ambiguity above

### D1. Repository layout (create exactly this)

```
contracts/
  awareness_event.schema.json            # JSON Schema draft 2020-12. THE one shared file. Holds the cls enum.
  fixtures/awareness_events.golden.json  # golden fixture, format in D3
  README.md                              # 20 lines: what the two files are, how each side runs the fixture
backend/
  pyproject.toml                         # uv project, requires-python ">=3.12", deps: pydantic>=2.7 ; dev: pytest, jsonschema>=4.20
  .python-version                        # 3.12
  src/cane/__init__.py
  src/cane/contracts/__init__.py
  src/cane/contracts/awareness_event.py  # Pydantic v2 model + enums + parse helper
  tests/contracts/test_golden_fixture.py # runs every fixture case through jsonschema AND Pydantic; both must agree with "expect"
  tests/contracts/test_enum_drift.py     # ObjectClass values == schema enum list, Source values == schema enum, RangeBand likewise
ios/CaneKit/
  Package.swift                          # swift-tools-version: 5.10 ; platforms .iOS(.v17), .macOS(.v14)
  Sources/CaneContracts/AwarenessEvent.swift
  Sources/CaneContracts/ObjectClass.swift   # (and Source / RangeBand enums — one file per enum or one Enums.swift, your call)
  Sources/CaneContracts/AwarenessEventValidation.swift
  Tests/CaneContractsTests/GoldenFixtureTests.swift
  Tests/CaneContractsTests/EnumDriftTests.swift
```
Add to the root `.gitignore`: `.venv/`, `__pycache__/`, `*.pyc`, `.pytest_cache/`, `.build/`, `*.xcuserstate`, `.swiftpm/`. Do not commit `backend/.venv` or `ios/CaneKit/.build`. Commit `backend/uv.lock`.

Toolchain on this Mac: `uv` is installed and has CPython 3.12 available (`uv sync` inside `backend/` creates `.venv`; run tests with `uv run pytest`). Swift 6.3.3 / Xcode 26.6: run `swift test` inside `ios/CaneKit`. The Swift tests run on macOS — do not use UIKit or iOS-only APIs in CaneContracts.

### D2. The schema — exact constraints

- `$schema`: `https://json-schema.org/draft/2020-12/schema`; `$id`: `https://cane.shellhacks/contracts/awareness_event.schema.json`; `title`: `AwarenessEvent`.
- `type: object`, `additionalProperties: false`, **all 11 keys required** (nullable ones must be present with explicit `null`). Rationale: catching a forgotten field is the fixture's whole job.
- Fields, in this order, with these constraints:
  1. `schema` — integer, `const: 1`
  2. `source` — string enum `["phone", "x4_cloud", "x4_local"]`
  3. `track_id` — string, `minLength: 1`
  4. `t_capture_ms` — integer, `minimum: 0` (phone-clock Unix epoch milliseconds, D-corrected)
  5. `ttl_ms` — integer, `minimum: 1`
  6. `cls` — string enum, exactly these 12 in this order: `person, cyclist, scooter, car, bus, pole, bollard, wall, parked_car, door, overhang, unknown_obstacle`
  7. `confidence` — number, `minimum: 0`, `maximum: 1`
  8. `bearing_deg` — number, `exclusiveMinimum: -180`, `maximum: 180` (torso-relative, 0 = ahead, + = right)
  9. `range_m` — `["number","null"]`, `minimum: 0`
  10. `range_band` — `["string","null"]` with enum `["near","mid","far", null]`
  11. `closing_mps` — `["number","null"]` (negative = receding, allowed)
- Conditional rule (X4 never states metres): `if` `source` is not `"phone"` `then` `range_m` must be `null`. Express with draft-2020-12 `if`/`then`.
- There is deliberately NO `urgency` field. A fixture case carrying `urgency` must be rejected by all three validators (schema, Pydantic, Swift).
- `range_m` and `range_band` may both be null (existence-only event). Both may be non-null on a phone event.

### D3. Golden fixture format

```json
{
  "schema": 1,
  "cases": [
    { "name": "phone_person_metric", "expect": "valid",   "event": { ...11 fields... } },
    { "name": "x4_states_metres",    "expect": "invalid", "reason": "range_m must be null unless source is phone", "event": { ... } }
  ]
}
```
Minimum content — at least 8 valid and at least 10 invalid cases, covering: valid phone event with `range_m` and `range_band` both set; valid phone event unanchored (`range_m` null, `range_band` set); valid `x4_cloud` with `range_band` + positive `closing_mps`; valid `x4_local`; valid existence-only (both null); valid `bearing_deg` exactly `180`; valid negative bearing; valid `unknown_obstacle`; valid negative `closing_mps`; invalid `bearing_deg` exactly `-180`; invalid `bearing_deg` `181`; invalid `ttl_ms` `0`; invalid `cls` `"bicycle"`; invalid extra field `urgency`; invalid `x4_cloud` with `range_m` `4.2`; invalid `confidence` `1.5`; invalid missing `track_id`; invalid `schema` `2`; invalid `range_band` `"close"`; invalid empty `track_id` `""`; invalid `source` `"laptop"`. Use realistic values (`t_capture_ms` ≈ 1790000000000, TTLs from the §4.4 table for the class).

### D4. Python model

- `class Source(StrEnum)`, `class ObjectClass(StrEnum)`, `class RangeBand(StrEnum)` in `awareness_event.py` (Python 3.12 has `StrEnum`).
- `class AwarenessEvent(BaseModel)` with `model_config = ConfigDict(extra="forbid", frozen=True)`; fields typed to match D2 (`Annotated[int, Field(ge=0)]`, `Annotated[float, Field(gt=-180, le=180)]`, etc.); a `@model_validator(mode="after")` enforcing the X4-never-metres rule.
- `schema: Literal[1]`.
- A helper `parse_awareness_event(payload: bytes | str | dict) -> AwarenessEvent` that raises `AwarenessEventError` (subclass of `ValueError`) wrapping `pydantic.ValidationError` / `json.JSONDecodeError` — "rejected, not crashed": callers catch one exception type.
- Immutability: frozen model; never mutate.

### D5. Swift model

- `public enum Source: String, Codable, CaseIterable`, `public enum ObjectClass: String, Codable, CaseIterable`, `public enum RangeBand: String, Codable, CaseIterable`.
- `public struct AwarenessEvent: Codable, Equatable, Sendable` with the 11 stored properties, `let`s, Swift names in camelCase (`tCaptureMs`, `ttlMs`, `bearingDeg`, `rangeM`, `rangeBand`, `closingMps`, `trackId`) mapped via `CodingKeys` to the wire names above. `cls` stays `cls`.
- `public static func decode(_ data: Data) throws -> AwarenessEvent` — throws `AwarenessEventError` (an `enum ... : Error, Equatable` with cases such as `.unknownKeys([String])`, `.missingKey(String)`, `.outOfRange(field: String)`, `.x4StatedMetres`, `.malformedJSON`). Never traps. Implement in the custom `init(from decoder:)`: (a) read the container's `allKeys` with a dynamic `CodingKey` struct and reject keys outside the 11; (b) require all 11 keys present — use `decode(Double?.self, forKey:)` for nullable fields so a *missing* key throws but an explicit `null` decodes; (c) range-check `bearingDeg`, `ttlMs`, `confidence`, `tCaptureMs`, `rangeM`; (d) enforce X4-never-metres; (e) `schema == 1`.
- `func encoded() throws -> Data` round-trips (encode → decode equals original) — add one test for that.

### D6. Tests (TDD — write each test, watch it fail, then implement)

- Python `test_golden_fixture.py`: parametrize over fixture cases (by `name`). For each: `jsonschema.Draft202012Validator(schema).is_valid(event) == (expect == "valid")` AND Pydantic `parse_awareness_event(event)` succeeds iff `expect == "valid"`. Both assertions in the same test so a disagreement between schema and model fails loudly.
- Python `test_enum_drift.py`: `[c.value for c in ObjectClass] == schema["properties"]["cls"]["enum"]` (same for `source`, and `range_band` minus the null).
- Swift `GoldenFixtureTests`: load the fixture via a path computed from `#filePath` (`../../../../contracts/fixtures/awareness_events.golden.json`); for every case, `decode` succeeds iff `expect == "valid"`; on invalid cases assert the thrown error is an `AwarenessEventError`. Loop over cases and fail with the case name in the message (XCTest has no parametrize; that is fine).
- Swift `EnumDriftTests`: read the schema JSON with `JSONSerialization`, compare `ObjectClass.allCases.map(\.rawValue)` to the schema's `cls` enum array (and Source, RangeBand likewise).
- Acceptance from the runbook: both suites pass the same fixture; an invalid event is rejected rather than crashed on; a schema version field is present.

### D7. Out of scope
Any detector or feedback logic. Any WebSocket/transport code (step 3). TTL lookup helpers (step 9). Do not add CI config.

## E. Global constraints (verbatim, from the spec)

- "urgency is NOT on the wire; range_m is never populated by the X4."
- "Eleven fields. All three lanes code against this."
- "Closed class enum (both sides must agree, so it lives in one file)."
- "Validate — schema version, enum membership, bearing in (-180, 180], ttl > 0. Reject and log; never crash on a malformed event."
- "A golden fixture file: ~10 events, valid and invalid, that both sides' test suites run against."
- Commit message format: `<type>: <description>` (types feat/fix/test/docs/chore). No attribution trailers.
- Coding style: immutable data (frozen models, `let`), small focused files, explicit error handling, no hardcoded magic values outside the schema/enums.

## F. Architect amendments — ACCEPTED and BINDING

Read `task-2-design.md` (same directory) **§2 Amendments A1–A7 in full** before writing any code. They are binding; where they conflict with section D above, the amendment wins. Highlights so nothing is missed:
- `t_capture_ms` window `[1600000000000, 4000000000000]`; `ttl_ms` `[1, 15000]`; `range_m` `[0, 50]`; `closing_mps` `[-60, 60]`; `track_id` length `[1, 64]`; NaN/Infinity rejected everywhere (Python `parse_constant` hook + `allow_inf_nan=False`).
- Conditional in the exact `if {source const phone, required} / else {range_m null}` form.
- Five `description` semantics (A4) in the schema **and** in `contracts/README.md`, plus the COCO→enum mapping table.
- Pydantic `strict=True`; observable round-trip test with exactly 11 keys; never `exclude_none`.
- Swift hand-written `encode(to:)` using `encode` not `encodeIfPresent`; `ContractPaths.swift` helper with `#filePath`; explicit-null vs absent-key tests.
- Fixture: `fixture_version`, `malformed_payloads` raw-string array, envelope sanity assertions, `reason` on invalid only, number formatting rule, and the additional cases listed in A7.
- Drift tests compare **every numeric bound** and the enums against the schema; `check_schema` test; `conftest.py` resolves repo root once.
- Commit in three commits so partial work is recoverable: (1) `feat: awareness event schema + golden fixture`, (2) `feat: python contract model + fixture tests`, (3) `feat: swift CaneContracts + fixture tests`.
