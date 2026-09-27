# X4 Sphere Coverage

## Problem
The Insta360 X4 on the wearer's shoulder captures the whole sphere (360° wide, 180° tall), but the
laptop bridge forwards only a 60°-tall band around the horizon (latitude −30° to +30°) to every
consumer: cloud YOLO, the local fallback, the depth/steering head and the scene VLM. At shoulder
height (≈1.4 m) the floor first becomes visible 2.4 m away, so a bag, dog, bollard or curb closer
than that to the side or behind the walker is invisible to the 360 camera, and the chest phone only
covers ±37° in front. Left unsolved, "360 awareness" is really a 360-wide, 60-tall ring, and the
seated "what is on the table" question cannot be answered.

## Evidence
- Live screenshot, 2026-09-26: the six labelled views cut off at the table edge while the Insta360
  app shows the whole scene. Arnav: "it's not able to detect the table like what's on the table even
  though the 360 app is able to show everything in view".
- Measured 2026-09-26 on the bridge's own remap tables (2880×1440 frame, 640 px crops): every crop
  covers latitude −30.0° to +30.0°, at all six yaws.
- `docs/steps/assets/step-06-live-equirect.jpg` vs `step-06-live-crops.jpg`: the equirect shows the
  full table and bottles; each crop truncates them at its bottom edge.
- `docs/shepherd-steering-plan.md`, risk 1: "a 60° crop only sees the floor between 2.42 m and
  3.49 m".
- Spec gap, not a regression: `camera-sync-design.md` §1.3 and the step-06 brief F3 specify six 60°
  square crops and never state a vertical requirement; the decision log has no entry on vertical
  coverage.

## Users
- **Primary**: a walking white-cane user wearing the shoulder-mounted X4, when a low obstacle (bag,
  dog, bollard, curb, chair leg) is within ~2.5 m to the side or rear, outside the chest phone's
  ±37° front cone. Trigger: the obstacle enters the warning envelope without ever having been boxed.
- **Secondary**: the same user seated, asking "what's around me" or "find my bottle"; table-top
  objects within arm's reach are outside every current view.
- **Not for**: front-cone hazards (the phone's job), metric range (the X4 never states metres),
  OCR and sign reading (needs rectilinear phone frames), drop-offs (cut from MVP, decision log
  2026-09-26).

## Hypothesis
We believe **forwarding the full sphere the X4 already captures, with the near floor guaranteed
first** will **warn the walker about low obstacles to the side and rear before they are 1 m away**
for **shoulder-mounted X4 wearers**.
We'll know we're right when **a floor object 1 m from the wearer at 90° and at 180° produces a hazard
event, and table-top objects at arm's length appear in "describe around me"**, while the existing
latency and range acceptance numbers still hold.

## Success Metrics
| Metric | Target | How measured |
|---|---|---|
| Vertical coverage of detector input, every bearing | ≥ 55° below the horizon (floor at 1 m for h = 1.4 m); stretch: full −90° to +90° | Remap-table latitude test, same method as the 2026-09-26 measurement |
| Side and rear floor hazard, live | Floor object 1 m from the wearer at 90° and 180° yields an AwarenessEvent within 2 s, 5 of 5 trials | Live X4 walk test, recorded events |
| Table-top objects in scene description | Bottle at arm's length named by "describe around me", 5 of 5 | Live query test |
| No latency regression | Frame age at send p95 < 50 ms; X4→L4 round trip p95 ≤ 300 ms at the demo rung | Existing bridge stats line |
| No range regression | Cyclist-sized object at 20 m still detected | Existing YOLO benchmark on the recorded walk |
| No self-detections | 0 person events from the wearer's own body over a 60 s stand-still | Live test, event log |

## Scope
**MVP** — the detector input at every bearing reaches at least 55° below the horizon, verified by
the remap test and the two live tests, with the latency and range metrics unchanged.

**Out of scope**
- Sky above +30° (signs, ceilings, overhead) — no walker hazard class lives there; stretch only if
  the whole sphere ships.
- Metric range from the X4 — unchanged rule, bands only.
- Drop-offs — cut from MVP per the 2026-09-26 decision.
- Phone-side changes to the event contract — bearings stay torso-relative; a detection's elevation
  is not a new event field unless /plan shows it is required.

## Delivery Milestones
<!-- Business outcomes, not engineering tasks. /plan turns each into a plan. -->
<!-- Status: pending | in-progress | complete -->

| # | Milestone | Outcome | Status | Plan |
|---|---|---|---|---|
| 1 | Floor visible offline | The step-06 equirect's table contents and a floor object appear in what the detector receives, at every yaw; the remap coverage test is green | in-progress | `.claude/plans/x4-sphere-coverage.plan.md` |
| 2 | Floor hazard warned live | A side or rear floor object at 1 m produces a hazard cue on the phone; latency and range metrics unchanged | pending | — |
| 3 | Whole sphere seen | Nothing in the equirect is discarded; the wearer's own body is suppressed; scene description sees table tops | pending | — |

## Open Questions
- [ ] Which constraints are frozen for the demo: the six-crop cloud API, the rung-1 upload budget
      (≈6.5 Mbps), or the current pixel density on far hazards (10.7 px/deg)? Not answered at PRD
      time — TBD, needs Arnav's ruling before /plan commits to an approach.
- [ ] Is the demo wearer's shoulder height 1.4 m (steering plan) or 1.5 m? Moves the 1 m floor
      target by about ±5°.
- [ ] Does "whole sphere" include the wearer's own head and shoulder in the nadir and zenith? They
      will be in view and are a person-class false-positive source.
- [ ] The steering and depth head fit the ground rows assuming horizon-level crops. Must steering
      move with the new coverage, or does milestone 2 leave steering on the current band?
- [ ] What input shapes does the deployed L4 engine accept? Bounds the option space; validate on
      the VM, not from memory.

## Risks
| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| Range loss: more floor and sky per pixel means fewer pixels on far hazards | High | Medium | Range metric is a gate; keep the 20 m cyclist benchmark |
| Upload or latency growth steps the health monitor down a rung | Medium | Medium | Latency metric is a gate; test at the demo rung |
| Blast radius: cloud API, bearing math, 48-column gap ring, VLM prompt and local fallback all assume the current geometry | High | High | Milestone 1 is offline and contract-visible; run the full backend and CaneKit suites before milestone 2 |
| Own-body false positives (shoulder, head, cane) once the nadir is in view | High if nadir included | Medium | Self-detection metric; treat as a milestone-3 blocker |
| Depth and ground-plane fit (steps 22, 27) break when the horizon moves inside the crop | Medium | Medium | Open question 4; keep steering on the current band until re-measured |
| Time: the demo is 2026-09-27 | High | High | Milestones 1 → 2 only; milestone 3 is stretch |

---
*Status: DRAFT — requirements only. Implementation planning pending via /plan.*
