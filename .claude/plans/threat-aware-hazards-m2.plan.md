# Plan: Threat-Aware Hazard Detection — milestone 2

**Source PRD**: `.claude/prds/threat-aware-hazards.prd.md`
**Selected Milestone**: 2 — Path-conflict gate
**Complexity**: Medium

## Summary

Stop announcing "Stop!" for vehicles that will miss. A moving hazard whose bearing is *sweeping* is
going past; one that holds bearing while closing is on a collision course. Cap urgency 3 to 2 when
the bearing sweeps faster than walking or turning can explain.

## The blocking open question, resolved

The milestone-1 plan flagged: *does the phone stream torso yaw rate to the feedback manager?*

**No, and there is no yaw source at all.** There is no CoreMotion anywhere in the tree; every
heading in the repo lives in `CaneNavigation`, which is walled off from the awareness path on
purpose (`CLLocation.course` is unreliable below 0.5 m/s — exactly when standing at a kerb). So the
gate cannot subtract a measured rotation, and the design has to survive without one.

Three things make that acceptable:

1. **Separation by magnitude.** A car at its urgency-3 moment (3.5 m lateral, 13.4 m/s) sweeps at
   `ω = v·d/r² ≈ 110 °/s`. Walking gait sways the torso by ~10 °/s; a deliberate turn peaks near
   90 °/s. A threshold at 45 °/s separates the car from walking outright, and only overlaps a fast
   deliberate turn.
2. **Common-mode rejection, when it is available.** Torso rotation moves *every* track's bearing by
   the same amount; a real passing car moves differentially. Subtracting the median bearing rate of
   the other tracks in view cancels rotation exactly, with no gyro. The same trick the bridge
   already uses on affine-invariant depth, where `relative_freedom` cancels both `a` and `b`.
   With fewer than two other tracks there is no estimate, and the raw rate is used against the
   conservative threshold above.
3. **The cap is one rung, never silence.** Urgency 3 becomes urgency 2, which still speaks —
   *"ahead to the right, car fast"*. A wrong suppression costs the word "Stop", not the warning.
   That is what bounds the harm of getting this wrong.

**No new state.** `AcceptedRecord` already carries `key`, `bearingDeg` and `tCaptureMs`, retained
1200 ms with a 64-record cap, which at 3 fps is three to four samples per track. The gate is a pure
function over state the gauntlet already keeps.

## Patterns to Mirror

| Category | Source | Pattern |
|---|---|---|
| Pure stage over state | `ios/CaneKit/Sources/CaneFeedback/RefractoryGate.swift` | A stage is an `enum` of static functions taking `(event, state, now)` and returning a decision; no stored state |
| Decision shape | `ios/CaneKit/Sources/CaneFeedback/UrgencyDecision.swift` | `UrgencyDecision(urgency:driver:isHardZero:)` — carry *why*, not just the level |
| Demote, never silence | `UrgencyPolicy.confidenceGated` | Confidence demotes by at most one rung and never below `aware`; the cap mirrors it exactly |
| Angle arithmetic | `ios/CaneKit/Sources/CaneFeedback/AngularMath.swift` | `absDelta` — the only place wrapping is done; a bearing difference must never be computed with plain subtraction |
| Named demo knob | `UrgencyPolicy.staticActM` | Thresholds are `public static let` on the policy type with the derivation in the doc comment |
| Counters | `ios/CaneKit/Sources/CaneFeedback/FeedbackCounters.swift` | Every suppression increments a named counter |
| Tests | `ios/CaneKit/Tests/CaneFeedbackTests/WalkTimelineTests.swift` | Scripted approach geometry, asserted as an exact sequence |

## Files to Change

| File | Action | Why |
|---|---|---|
| `ios/CaneKit/Sources/CaneFeedback/BearingRate.swift` | CREATE | Pure: per-track sweep rate from `recentAccepted`, plus the common-mode estimate |
| `ios/CaneKit/Sources/CaneFeedback/PathConflict.swift` | CREATE | The gate: cap urgency 3 → 2 when the corrected sweep exceeds the threshold |
| `ios/CaneKit/Sources/CaneFeedback/Gauntlet.swift` | UPDATE | Apply the cap between `UrgencyPolicy.decide` and `Hysteresis.apply` (line 158) |
| `ios/CaneKit/Sources/CaneFeedback/FeedbackCounters.swift` | UPDATE | `pathConflictCapped` |
| `ios/CaneKit/Sources/CaneFeedback/UrgencyDecision.swift` | UPDATE | A `DrivingMetric` case for a capped decision, so the driver still explains itself |
| `ios/CaneKit/Tests/CaneFeedbackTests/BearingRateTests.swift` | CREATE | Rate maths, wrapping, insufficient samples, common-mode |
| `ios/CaneKit/Tests/CaneFeedbackTests/PathConflictTests.swift` | CREATE | The gate's decisions, including every fail-open path |
| `ios/CaneKit/Tests/CaneFeedbackTests/PassingCarTests.swift` | CREATE | The PRD's geometry table, walked as a scripted timeline |

## Tasks

### Task 1: Bearing rate
- **Action**: `BearingRate.forTrack(key:in:excluding:)` → °/s from the two most recent records of
  that key with distinct capture stamps, `nil` under two samples or a non-positive Δt.
  `BearingRate.commonMode(excluding:in:)` → median rate across other tracks, `nil` under two.
- **Mirror**: `AngularMath.absDelta` for wrapping; `RecentAccepted` for the record shape.
- **Validate**: `swift test --filter BearingRateTests`

### Task 2: The gate
- **Action**: `PathConflict.capped(_:event:records:now:)`. Cap only `.immediate`, only for
  non-static classes, only when a corrected rate exists and exceeds `sweepThresholdDegPerS`.
  Everything else returns the decision untouched.
- **Mirror**: `UrgencyPolicy.confidenceGated` for the demote-by-one shape.
- **Validate**: `swift test --filter PathConflictTests`

### Task 3: Wire it in
- **Action**: Apply in `Gauntlet.score` before hysteresis; increment `pathConflictCapped`.
- **Validate**: `swift test --filter CaneFeedbackTests`

### Task 4: The geometry, as a test
- **Action**: Walk the PRD's table — a car at 3.5 m lateral closing at 13.4 m/s must reach urgency 2
  and never 3; a head-on approach at the same speed must reach 3.
- **Validate**: `swift test --filter PassingCarTests`

## Validation

```bash
cd ios/CaneKit && swift test          # expect >= 526 passing, 0 failures
cd backend && uv run pytest -q        # untouched; must stay 1365 passed / 1 skipped
```

## Risks

| Risk | Likelihood | Mitigation |
|---|---|---|
| A real head-on threat is capped during a fast torso turn | Medium | The cap is one rung: urgency 2 still speaks the direction and the class. Common-mode rejection removes it whenever a second track is in view |
| Threshold is wrong for real traffic | High | It is a named constant with its derivation in the doc comment, and milestone 1's recordings can re-tune it offline without a re-walk |
| Bearing rate is noisy at 3 fps | Medium | Two samples minimum, distinct capture stamps, and `nil` rather than a guess — the same reject-never-clamp rule `motion.py` uses |
| The gate silently suppresses everything | Low | **High impact.** `pathConflictCapped` is counted, and take C of the walk exists precisely to catch it |

## Acceptance
- [ ] Tasks 1–4 complete
- [ ] `swift test` green at or above 526
- [ ] Backend untouched and still green
- [ ] A capped decision still speaks; nothing is ever demoted below `aware`
- [ ] Every fail-open path has a test
