# Threat-Aware Hazard Detection

## Problem

The awareness pipeline detects objects in 360° and estimates how fast they are
closing, but it has no model of whether a hazard will actually intersect the
user's path, and no representation at all for hazards in the ground plane. On
a sidewalk beside live traffic it fires a full urgency-3 Stop for every vehicle
that passes — including ones that miss by several metres — while staying
silent about the pothole the user is about to step into.

Both failures cost the same thing: trust. A Stop that means nothing teaches the
user to ignore the one that means everything, and a device that misses the
hazard that actually drops them is a device they stop wearing.

## Evidence

**Verified in-session by tracing the shipped code (2026-09-26):**

- A vehicle at 13.4 m/s passing with a 3.5 m closest-approach distance walks
  the urgency ladder to **3 (immediate)** — `near` band plus radial closing
  above the 3.0 m/s `fastClosingMps` threshold. It produces a 900 ms alarm
  rattle, a panned earcon and `"Right side!"` roughly 0.26 s before the vehicle
  draws level. The vehicle was never on a collision course.
- **Refractory suppression cannot absorb this.** Urgency 3 is defined with
  `refractory = 0` ("a Stop always speaks"), and per-track suppression keys on
  `track_id`, so every vehicle is a fresh track. Traffic at 10 vehicles/minute
  produces 10 Stops/minute.
- **`parked_car` is in the class enum but is never emitted.** The COCO mapping
  sends every `car` to the moving class unconditionally, so parked vehicles
  take the *moving* ladder with closing rate supplied by the user's own walking
  pace (~1.4 m/s, above the 0.5 m/s deadband). Inside 5 m that is urgency 2 —
  spoken — for every parked car on the block. The static ladder that was
  designed to cap them at a haptic-only cue is unreachable.
- **Nothing in the pipeline computes lateral miss distance.** `closing_mps` is
  radial by construction (derived from box-height range rate). It answers "is
  the distance between us shrinking" and not "will this occupy the space I am
  about to occupy." The two differ by exactly the miss distance.
- **Ground hazards have no representation anywhere.** The frozen 12-value class
  enum contains no pothole, curb, step or drop-off. `camera-sync-design.md`
  §2.3 ceded drop-off detection deliberately and documents why: they are a
  ground-plane discontinuity rather than an object, YOLO cannot box them, and
  monocular depth is weakest exactly there.
- **Depth cannot state how deep a hazard is.** Measured on real X4 frames:
  Depth Anything V2 is affine-invariant in disparity, a one-parameter ground
  scale fit was rejected on all three forward crops (residuals 0.686 / 0.922 /
  0.892 against a 0.25 threshold) while a two-parameter affine fit scored
  rms 0.068 / 0.005 / 0.013. Relative surface deviation is recoverable;
  centimetres are not, until the affine fit lands.

**Assumptions — not yet validated:**

- That false Stops actually drive users to disable or ignore the device, rather
  than being tolerated as the price of safety. Plausible from alarm-fatigue
  reasoning and from §2.3's own "a half-working curb detector is worse than
  none" position, but **needs validation via a blindfolded-listener session
  and, ideally, one O&M instructor's review.**
- That ground hazards rank above other unbuilt features in a cane user's own
  priority order. **Needs validation via user interview.** The counter-position
  is already written into §2.3: the white cane is very good at drop-offs, and
  "we extend the cane, we don't replace it" is the sharper story.
- Baseline announcement rate on a real traffic-adjacent walk is **TBD — needs a
  measured walk** before any reduction target can be called a win.

## Users

**Primary** — a blind or low-vision white-cane user walking an urban route on a
sidewalk adjacent to live vehicle traffic. Triggered on essentially every
outdoor city walk: this is not an edge case, it is the dominant context.

**Secondary** — the same user approaching a ground-plane discontinuity
(descending curb, pothole, missing pavement, unmarked step down) on a route
they have not memorised.

**Not for:**

- Wheelchair and mobility-scooter users — the ground-hazard set and the
  clearances are different, and nothing here is tuned for them.
- Users without a cane. The whole safety argument assumes the cane is still the
  primary ground sensor and this system extends its reach.
- Indoor navigation, transit and crosswalk-signal assist — already cut in
  `device-responsibilities.md`.

## Hypothesis

We believe **a path-conflict test on moving hazards, plus a forward
ground-discontinuity channel**, will **make every Stop mean "this will hit you"
and give warning of hazards the cane currently finds by falling into them**
for **white-cane users walking beside live traffic**.

We'll know we're right when **a fixed traffic-adjacent walk produces zero
urgency-3 announcements for vehicles passing wider than the miss-distance
threshold, still produces one for a staged head-on approach, and announces a
marked ground hazard before the cane tip reaches it.**

## Success Metrics

| Metric | Target | How measured |
|---|---|---|
| False Stops on the traffic walk | **0** across a 5-minute walk past ≥ 20 passing vehicles | Logged urgency-3 events reconciled against hand-annotated video |
| True Stop retained | **1 of 1** on a staged head-on approach (sighted walker standing in for the vehicle) | Staged approach, event log |
| Total announcements per minute, parked-car block | **≥ 50% below baseline** | Event log; baseline is TBD — needs the measured walk first |
| Ground-hazard lead time | Announced **≥ 1.5 m** before the cane tip reaches it | Taped-out hazard, measured on the floor |
| Ground-hazard false-positive rate | **≤ 1 per 5-minute walk** on intact pavement | Event log vs annotated video |
| Added end-to-end latency | **≤ 10 ms p95** | Existing latency harness; compare against the current p50 68–69 / p95 87–89 ms |
| Regression guard | No change to Stop behaviour on the existing head-on fixtures | Existing fixture suite stays green |

## Scope

**MVP** — the minimum to test the hypothesis on the existing X4 + L4 + iPhone
rig, inside the hackathon window:

1. A **path-conflict gate** that demotes moving hazards whose bearing is
   sweeping fast enough to imply they will miss. It must work **without metric
   range** (the X4 never sends metres), must **fail open** (no confidence in
   the geometry → no suppression), and must **demote rather than silence** — a
   suppressed threat still reaches the user at a lower rung.
2. A **forward ground-discontinuity channel** on the three forward crops that
   announces *that* there is a discontinuity and *where*, never how deep. It
   must never contradict the cane and must clear a confidence floor set high
   enough that §2.3's "worse than none" argument does not apply.
3. A **measured trust baseline** — the traffic-adjacent walk and the parked-car
   block, recorded before and after, so both claims above are evidence rather
   than assertion.

**Out of scope**

- **Metric depth of a ground hazard** ("a 12 cm drop") — measured as
  unrecoverable until the affine fit lands. The system says "step down ahead"
  or it says nothing.
- **Rear and lateral ground hazards** — the user is not walking backwards;
  forward crops only.
- **A trained pothole/curb detector** — no labelled dataset and no time to
  build one. Geometric only.
- **Roadway-versus-sidewalk semantic segmentation** — strictly more capable and
  it can subsume the geometric gate later, but it needs a model in the hot loop
  and it belongs to the SegFormer evaluation track.
- **Replacing the cane for drop-offs.** §2.3's position stands: this augments
  the cane's reach and never claims to substitute for it.
- **Reworking the `parked_car` mapping into a true moving/stationary
  classifier** — the path-conflict gate handles the stationary case as a side
  effect (a parked car's bearing sweeps as you walk past it). Revisit only if
  the walk shows it is not enough.
- **Crosswalk and traffic-signal assist** — already cut.

## Delivery Milestones

<!-- Business outcomes, not engineering tasks. /plan turns each into a plan. -->
<!-- Status: pending | in-progress | complete -->

| # | Milestone | Outcome | Status | Plan |
|---|---|---|---|---|
| 1 | Trust baseline measured | The traffic walk and parked-car block are recorded and counted, so every later claim has a before | in-progress | `.claude/plans/threat-aware-hazards.plan.md` |
| 2 | Path-conflict gate | Passing traffic no longer triggers Stops; a real collision course still does | complete (field validation in 4) | `.claude/plans/threat-aware-hazards-m2.plan.md` |
| 3 | Ground-discontinuity channel | The user is told about a drop-off or pothole ahead before the cane tip finds it | complete (field validation in 4) | `.claude/tdd/threat-aware-hazards-m3.tdd.md` |
| 4 | Trust baseline re-measured | Both claims are backed by a measured before/after on the same route | pending | — |
| 5 | *(post-hackathon)* Semantic roadway suppression | Hazards standing on the roadway are demoted by scene understanding, not geometry alone | pending | — |
| 6 | *(post-hackathon)* Metric ground-hazard depth | "Step down, about 15 cm" becomes sayable — depends on the affine scale fit (step 27) | pending | — |

## Open Questions

- [ ] **Does the phone stream torso yaw rate to the feedback manager today, at a
      usable rate?** Bearings are torso-relative by design, so when the user
      turns, *every* bearing sweeps and an uncompensated path-conflict gate
      would suppress real threats during a turn. If yaw is unavailable the gate
      must disable itself while turning — confirm the data exists before
      committing to milestone 2.
- [ ] **What is the miss-distance threshold?** A sidewalk-to-near-lane offset
      of ~3.5 m should be suppressed and a head-on approach must not be. The
      boundary between them is unknown and has to come off the measured walk.
- [x] **Does a new ground-hazard class break the frozen contract?**
      **Answered 2026-09-26: neither option was needed.** A hole has no metric
      range and no closing rate, so the urgency ladder scores it 1 — haptic only,
      never spoken — whatever class it wears. It therefore gets its own `ground`
      frame, as steering did in step 21, and `awareness_event.schema.json` stays
      frozen with its 12 classes untouched.
- [x] **How does a ground-hazard cue arbitrate against the steering cue?**
      **Answered: it does not have to.** A ground cue is presented on the
      `.hazard` channel, which preempts everything including `.steering`; the
      steering cue is the lowest thing in the house and is dropped while it
      plays.
- [x] **What confidence floor makes the ground channel better than silence?**
      **Set, but derived rather than measured**: `MIN_DROP_RATIO` 1.35 in the
      detector and `MIN_ANNOUNCE_CONFIDENCE` 0.35 on the laptop, together roughly
      "the floor reads twice as far away as it should". Both are named constants
      that milestone 1's recordings can re-tune offline.
- [ ] **Is the path-conflict decision the consumer's to make?** §4.6 is explicit
      that producers emit facts and the consumer computes urgency, which argues
      the gate lives with the consumer — and the yaw data is only there anyway.
      Confirm this reading before planning.

## Risks

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| The path-conflict gate suppresses a genuine threat (user turning, yaw uncompensated, erratic driver) | Medium | **High — a missed Stop is an injury** | Fail open: no yaw or low geometric confidence → no suppression. Demote by at most one rung, never to silence. Gate disables itself above a yaw-rate threshold. |
| Ground-hazard false positives train distrust, exactly as §2.3 predicted | High | High | High confidence floor; announce at urgency 1–2 and never as a Stop; ship behind a runtime kill-switch so the demo can survive without it |
| Contract change desyncs Swift and Python late in the build | Medium | High | Prefer `unknown_obstacle` for the MVP; only break the contract if the phrasing loss is judged unacceptable, and re-run step 2 in both languages if so |
| Ground detection cost blows the latency budget | Low | Medium | Depth on the three forward crops is already measured at ~92 ms and the gap-profile arithmetic at 1.69 ms; plane-fit outlier detection is the same order. Re-measure before merging. |
| Nobody who is actually blind validates any of this before the demo | High | Medium | Run the blindfolded-listener protocol the speech layer already references; state plainly in the demo that no blind user has tested it yet |
| Scope overruns the hackathon window and neither milestone lands | Medium | High | Milestones 1 and 2 are independently demoable. If time runs short, ship the path-conflict gate alone — it needs no new detector and no contract change. |

---
*Status: DRAFT — requirements only. Implementation planning pending via /plan.*
