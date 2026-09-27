# TDD Evidence — Threat-Aware Hazard Detection, milestone 3

**Milestone**: 3 — Ground-discontinuity channel
**Date**: 2026-09-26

## The two decisions that shaped it

**1. A hole cannot be an `AwarenessEvent`.** The urgency ladder scores on metric
range or a closing rate. A pothole has neither, so every ground hazard scores
urgency 1 — *haptic only, never spoken* — and the wearer is never told. That is
not a tuning problem, it is the ladder's shape. So step 29 gets its own `ground`
frame, exactly as step 21 gave steering one.

The payoff: `awareness_event.schema.json` and its 12-class enum stay **frozen**.
The PRD's open question about paying for a contract change answers itself —
nobody pays. The envelope's own description already licenses this: *"any other
unrecognised `type` … a receiver logs and ignores"*.

**2. It reports a ratio, never a depth.** Depth Anything V2 is affine-invariant
in disparity — your measurement, on real X4 frames — so no part of this system
can know a hole is 15 cm deep. It can know the floor reads 2.4x farther than a
flat floor would at that row. The contract says so, the frame says so, and
`GroundPhraseTests` asserts no digit ever reaches speech.

## Task report

| Task | RED | GREEN |
|---|---|---|
| Detector (`ground_hazard.py`) | `ModuleNotFoundError` | 14 passed |
| Wire model + `build_profile` | 2 guard tests failed (below) | 451 passed |
| Frame builder (`ground_frame.py`) | collection error | 12 passed |
| Transport + pipeline + schema | 1 guard test failed (below) | 1380 passed |
| Swift transport / gate / phrase | `cannot find 'GroundCue'` | 578 passed |
| App runtime | 2 compile errors (below) | BUILD SUCCEEDED |

## Three guards that fired, and were right to

1. **`test_the_profile_carries_no_bearing_no_metres_and_no_clock`** — an explicit
   allowlist of `GapProfile`'s fields. My `ground_hazard` field is a *column
   index* and a *ratio*, so it carries no bearing (the bridge derives that), no
   metres and no clock; the allowlist was extended with that reasoning recorded.
2. **`test_every_module_the_request_path_imports_is_guarded`** — walks the real
   import graph from `/infer` and demands every module be clock-free. The new
   detector reads no clock, so it was registered rather than exempted.
3. **`test_the_schemas_frame_count_matches_its_oneof`** — the schema's prose says
   "ten WebSocket frame shapes" and the guard pins that to `len(oneOf)`. Adding
   `ground` made it eleven and the word map only went to ten. Exactly the rot it
   was written to catch, on its second outing.

Plus two real compile errors in the app runtime: `settings` was only an `init`
parameter, never stored, so the ground loop in `startLoops` could not reach the
live haptic gain. Fixed by holding it as a property, the way `arbiter` already is.

## An architectural limit, stated rather than implied

The ground fit takes each row's **median across columns**, which is what lets a
hazard in one column be measured against the floor in the others. Inverted, that
is a ceiling: a discontinuity spanning more than half the crop becomes the
median, the fit follows it down, and the hazard hides itself. **A kerb running
across the whole frame is exactly that shape.** This detector finds holes in a
floor; it does not find the edge of a floor, and §2.3's position stands there.
It is in the module docstring and in a test's comment, not just here.

## Test specification (53 new tests)

| # | What is guaranteed | Where | Result |
|---|---|---|---|
| 1 | A flat floor, a shallow dip and a step **up** all yield nothing | `test_ground_hazard.py` (3) | PASS |
| 2 | A carved drop is found, at the right column, left or right | `test_ground_hazard.py` (3) | PASS |
| 3 | Span grows with width; confidence grows with depth and stays in 0..1 | `test_ground_hazard.py` (3) | PASS |
| 4 | No fit, a NaN map, no ground rows or a nonsense crop size all refuse | `test_ground_hazard.py` (5) | PASS |
| 5 | Local source, no profiles, clear floor, low confidence, behind the wearer → no frame | `test_ground_frame.py` (5) | PASS |
| 6 | The capture stamp is echoed, the bearing comes from the column, the mount offset rotates it | `test_ground_frame.py` (4) | PASS |
| 7 | The most confident hazard across crops wins; the ratio travels and no depth does | `test_ground_frame.py` (3) | PASS |
| 8 | A malformed frame is rejected, not trapped: unknown key, −180 bearing, ratio ≤ 1, bad confidence, zero span, bad source | `GroundCueTests` (6) | PASS |
| 9 | The envelope routes `ground`; the frame carries no depth key | `GroundCueTests` (3) | PASS |
| 10 | First sighting speaks; the same hole is throttled; it re-arms on time or on a big bearing change | `GroundGateTests` (5) | PASS |
| 11 | An expired cue is dropped and leaves the state alone; the re-arm check wraps | `GroundGateTests` (2) | PASS |
| 12 | **Direction first, and the word is "drop" — never "step"** | `GroundPhraseTests` (2) | PASS |
| 13 | **No digit is ever spoken**, and every phrase fits the 6-word budget | `GroundPhraseTests` (2) | PASS |
| 14 | The cue is directional and never the urgency-3 alarm; without haptics it still speaks | `GroundPhraseTests` (2) | PASS |

## Why the word is "drop"

`SpeechPhrase`'s rule is that every phrase is locative or a self-contained
imperative, **never a steering command**, because the system does not model free
space. "Step down" fails that twice: it is an instruction, and it instructs the
exact action that hurts if the hole is deeper than a step. The PRD's own prose
said "step down"; the output layer's discipline wins, and `GroundPhraseTests`
pins it.

## Coverage and known gaps

```
backend      1380 passed, 3 skipped
ios/CaneKit   578 passed, 0 failures
CaneApp       BUILD SUCCEEDED (unsigned; Config/Local.xcconfig still absent)
```

- **No field validation.** Every depth map here is synthetic. A real pothole has
  texture, shadow and a wet patch, and none of that is in these fixtures.
  Milestone 4 is where this meets pavement.
- **Thresholds are derived, not measured.** `MIN_DROP_RATIO` 1.35 and
  `MIN_ANNOUNCE_CONFIDENCE` 0.35 come from geometry and §2.3's err-toward-silence
  argument, not from a walk.
- **Wide hazards hide themselves** — see the limit above.
- The app was compiled, not run: signing needs a development team.
