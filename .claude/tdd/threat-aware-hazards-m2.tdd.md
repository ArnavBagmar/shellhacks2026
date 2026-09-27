# TDD Evidence — Threat-Aware Hazard Detection, milestone 2

**Source plan**: `.claude/plans/threat-aware-hazards-m2.plan.md`
**Milestone**: 2 — Path-conflict gate
**Date**: 2026-09-26

## User journeys

1. As a wearer on a sidewalk, I want no "Stop!" for vehicles that will pass me, so the Stops I do
   hear mean something.
2. As a wearer, I want a vehicle actually coming at me to still stop me, whatever else changes.
3. As a wearer, I want a suppressed Stop to still warn me — one rung down, not silence.

## Task report

### Task 1 — `BearingRate`

- **RED**: `swift test --filter BearingRateTests` → `cannot find 'BearingRate' in scope`.
- **GREEN**: `Executed 12 tests, with 0 failures`.
- Later extended to 15 tests when the interval bounds were added (below).

### Task 2 — `PathConflict`

- **RED**: `cannot find 'PathConflict' in scope`.
- **GREEN**: `Executed 12 tests, with 0 failures`.

### Task 3 — Wiring, and a bug the existing suite caught

Wiring the gate into `Gauntlet.score` broke
`PumpTests.testUrgencyThreePenetratesAFullQueueAndBothRefractories`:

```
XCTAssertEqual failed: ("Optional(2)") is not equal to ("Optional(3)")
  - a Stop is never queued behind chatter
```

**This was a real defect, not a stale test.** That fixture moves a track from 170.0° to 171.0° across
**20 ms** — one degree of box-centre jitter, which my rate maths reported as 50 °/s and the gate read
as a miss. `BearingRate` had no minimum interval, while `cloud/motion.py` has exactly that guard
(`MIN_DT_MS`) for the identical reason on range rate. I had not mirrored it.

Fixed by adding `minIntervalMs = 100` (derived from ~1° of bearing jitter: `1.41/dt` puts the error
at ~14 °/s, well under the 45 °/s threshold) and `maxIntervalMs = RecentAccepted.retentionMs`, both
with their own tests. The pump test then passed untouched — the fix restored its behaviour rather
than the test being rewritten to accept mine.

### Task 4 — The geometry

`PassingCarTests` derives bearing, range band and closing rate from one vehicle's position, so no
number is hand-tuned toward an answer. The **only** difference between the two cases is lateral offset.

| case | lateral | closing when `near` | sweep | urgency |
|---|---|---|---|---|
| passes on the road | 3.5 m | 7.8 m/s | ~84 °/s | **2** (capped) |
| collision course | 0.1 m | 13.4 m/s | ~4 °/s | **3** |

Passed on the first run.

## Test specification

| # | What is guaranteed | Test | Result |
|---|---|---|---|
| 1 | Sweep in °/s from two sightings, signed, wrapped at the antimeridian | `BearingRateTests` (3) | PASS |
| 2 | One sighting, a shared capture stamp, or an untrustworthy interval yields no rate | `BearingRateTests` (4) | PASS |
| 3 | Only the two most recent samples count; other tracks do not contribute | `BearingRateTests` (2) | PASS |
| 4 | Common mode is the median of ≥2 other tracks, else nil | `BearingRateTests` (2) | PASS |
| 5 | Corrected subtracts rotation, or falls back to the raw rate | `BearingRateTests` (4) | PASS |
| 6 | A sweeping Stop is capped to act-soon, either direction | `PathConflictTests` (2) | PASS |
| 7 | **A head-on approach keeps its Stop** | `PathConflictTests` | PASS |
| 8 | Urgency 2, 1 and static classes are never touched | `PathConflictTests` (3) | PASS |
| 9 | **Fail open**: no records, a first sighting, or a sweep exactly at threshold keeps the Stop | `PathConflictTests` (3) | PASS |
| 10 | A torso turn, seen against two other tracks, does not cap a real threat | `PathConflictTests` | PASS |
| 11 | The capped decision keeps its driver and hard-zero flag | `PathConflictTests` | PASS |
| 12 | A vehicle passing 3.5 m away never reaches urgency 3 | `PassingCarTests` | PASS |
| 13 | **A capped Stop is still spoken** — one rung, not silence | `PassingCarTests` | PASS |
| 14 | The same vehicle head-on still reaches urgency 3 | `PassingCarTests` | PASS |

## Coverage and known gaps

```
ios/CaneKit   556 passed, 0 failures   (was 526)
backend      1365 passed, 1 skipped    (untouched)
```

- **The threshold is derived, not measured.** 45 °/s comes from geometry and plausible torso rates,
  not from a real walk. Milestone 1's recordings can re-tune it offline with no second walk — that is
  most of why milestone 1 came first.
- **Common-mode rejection needs two other tracks.** A lone car on an empty street is judged on its
  raw sweep. That is the intended fallback, and the conservative threshold is what makes it safe.
- **No field validation.** Everything here is synthetic geometry. Milestone 4 is where this meets
  real traffic, and take C of the walk is the guard against a gate that caps everything.
