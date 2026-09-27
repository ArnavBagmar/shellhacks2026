# Win ShellHacks — make the 360° thesis true, correct and audible

**Status:** DRAFT — requirements only. Implementation planning pending via `/plan`.
**Created:** 2026-09-26 · **Horizon:** the remaining hours of ShellHacks 2026 (judging Sep 27)
**Benchmark:** [tonywangs/shepherd](https://github.com/tonywangs/shepherd) — TreeHacks 2026 Grand Prize
($12k) plus the OpenEvidence Healthcare track plus Best Use of Clinical Information.

> Scope decision (user, 2026-09-26): this PRD is scoped to **winning ShellHacks**, not to the
> post-hackathon product. Where a finding matters beyond the deadline it is recorded under Risks
> rather than silently dropped.

---

## Problem

On the axis that decides an assistive-device panel, we are ahead of the project that won TreeHacks:
we see 360°, we hold identity and closing speed on a hazard out to 15 m, and we can answer questions
about the scene with real bearings. Shepherd can do none of that and no amount of tuning will get
them there — their sensor is one forward cone.

But on the day we lose, because three things are true at once: **the flagship steering feature emits
nothing**, **every failure mode presents to the wearer as silence — identical to "all clear"**, and
**the first sentence of our README describes a capability that does not exist in the code**. The cost
of leaving this unsolved is not a lower score; it is that a judge who greps for one symbol concludes
we overclaimed on a safety device, which is a worse verdict than "incomplete".

## Evidence

All citations verified against `main` on 2026-09-26 by a 46-agent audit in which every material
finding was put to an independent adversarial refutation pass; 33 of 36 survived.

- **The steering port emits zero cues on the working branch.** `feat/build` is 0 ahead / 12 behind
  `main` and carries a half-ported tree: `cloud/depth/profile.py:249` calls `detect_ground_hazard`,
  which is undefined here, so every crop raises `NameError`, `cloud/depth/head.py:143-146` swallows
  it, and no `steer` frame is ever sent. Measured: backend collection aborts, 23 failed. On `main`:
  1434 passed. The launcher reports "depth is ready" throughout, because `/warmup` never builds a
  profile.
- **No on-device perception exists.** `grep -rniE "CoreML|import Vision|VNCoreML|MLModel"` over
  `ios/CaneKit/Sources ios/CaneApp/Sources` returns nothing. `FrontCamera.addConsumer` has zero
  callers. The phone never constructs an `AwarenessEvent`. Therefore `range_m` is null on every event
  the product emits, and `README.md:3` ("A chest-mounted iPhone detects front hazards on-device") and
  `docs/device-responsibilities.md:157` ("If it dies, front warnings keep working") are both false.
- **The unanchored ring can point into an obstacle.** `bridge/steer_frame.py` still builds the ring
  from three crops when the ground fit is rejected, comparing incommensurable affine units. A
  verifier reproduced it by executing shipped code: a left obstacle at 3 m with 8 m open ahead yields
  `steer = -0.289`, bearing −30°, `blocked = False` — 30° into the obstacle. Named by our own
  `docs/shepherd-steering-plan.md` §3.5 ruling 1; step 29 is open.
- **`proximity` is taken over the whole ±90° arc**, so a wall 0.6 m off the shoulder pins the cue at
  maximum for an entire corridor and, via the `(1 − proximity)` navigation blend, silently zeroes
  turn guidance in any corridor narrower than ~5 m.
- **`STEER_TTL_MS = 400`** (`transport/messages.py:228`) is shorter than the measured tick period
  (333 ms) plus RTT, so the phone sits in failsafe decay roughly half of all ticks.
- **Three silent-death paths.** A mid-walk X4 USB drop — a documented precondition, the camera drops
  off USB when its screen sleeps — is silent for ~151 s and never recovers. `hasConnection` is
  `task != nil` (`EventLinkClient.swift:109`), so a wrong address reads "connected" for ~5 minutes.
  A dead bridge on a live socket is indistinguishable from a clear path.
- **Shepherd's counter-position, from their repo and Devpost:** ~$50 BOM with STLs and an Onshape
  link, a motor that physically steers the cane, true LiDAR metres, no cloud, <150 ms worst case,
  and a build guide a stranger can follow. 63 files, zero automated tests.
- **No blind user or O&M professional has evaluated this.** `grep` for participant / IRB / consent /
  O&M / NFB / Lighthouse across all docs returns nothing; the only planned protocol is
  blindfolded-sighted and its harness does not exist. — *Assumption that our haptic vocabulary is
  legible at all; needs validation via a timed identification trial with human subjects.*

## Users

- **Primary: a ShellHacks judge at the table with ~4 minutes.** Sighted, technical, has seen ten
  other projects, and on an assistive-device pitch will ask three questions in this order: what does
  it do that the cane doesn't, what happens when it fails, and has a blind person used it.
- **Depicted: a blind or low-vision pedestrian** walking a known route with their own cane. They are
  the subject of the demo and the arbiter of whether a claim is honest, but they are not a user of
  this build — nobody in this population has touched it.
- **Not for:** independent daily use today. The rig is a chest phone, a shoulder camera, a tethered
  laptop and two power banks, and it cannot be donned or calibrated without sighted help.

## Hypothesis

We believe **making the 360°-only sensor thesis true, correct and audible** — a steering cue that
actually fires and points at the passable side, a device that says out loud when it has stopped
seeing, and a pitch that claims only what the code does — will **convert an architecture that is
already ahead of the TreeHacks winner into a verdict that reflects it** for **the judging panel**.

We'll know we're right when **a judge can walk up behind the wearer unannounced and hear the device
name them before they arrive; can pull the USB cable and hear the device say so; and cannot find a
claim on our pitch surface that the code does not support.**

## Success Metrics

| Metric | Target | How measured |
|---|---|---|
| Steer cues produced on a live walk | > 0, and ≥ 80% of ticks carry a live (non-decayed) cue | Bridge stats line + phone-side tally on a recorded walk |
| Cue points at the passable side | 3 of 3 set pieces (blocked-side corridor, doorway, person in path) | Step 25 walk protocol, wearer takes the passable side unaided |
| Continuous-buzz regression | No sustained max-rate cue in a corridor ≥ 2 m wide | Same walk, pulse-rate log |
| Time from sensing loss to the wearer being told | ≤ 5 s, spoken | Pull the USB cable mid-walk, stopwatch |
| False "connected" state | 0 — link state reflects frames arriving, not socket existence | Point the app at a wrong address, observe |
| Unsupported claims on the pitch surface | 0 | Grep audit of README + docs + deck against the tree |
| "Walk up behind him" beat | Lands on 3 of 3 rehearsals | Rehearsal tally |

## Scope

**MVP — the minimum that tests the hypothesis**

1. The system produces steering cues at all, on a branch whose tests pass, with a preflight that
   proves a real depth profile rather than trusting a readiness boolean.
2. The steering cue is *correct*: it points at the passable side when scale is unrecoverable, it
   does not saturate because of something beside the shoulder, and it stays live between sensor
   frames instead of decaying to neutral half the time.
3. The wearer is told, out loud, whenever the device has stopped seeing — and the app's notion of
   "connected" means frames are arriving.
4. The pitch claims only what the system does, and leads with the one capability Shepherd
   structurally cannot reach.

**Positioning decision (user, 2026-09-26):** we **answer** the on-device question rather than build
it. The false on-device claim is deleted and replaced with the sensor thesis — *the phone is the
output device, the 360° ring is the sensor, and that trade buys rear coverage no phone-only device
can have.* Building a CoreML detector on the phone is explicitly **not** in this PRD.

**Out of scope**

| Item | Why deferred |
|---|---|
| On-device CoreML detector (steps 4, 5) | Positioning decision above — answered, not built |
| Legal, privacy and security findings — Ultralytics AGPL-3.0 served over HTTP with no LICENSE file; unauthenticated WebSocket on `0.0.0.0` that evicts the existing phone on any new connection; cleartext speech transcripts and stored bystander imagery | User decision: post-hackathon. Recorded under Risks — the eviction behaviour is reachable by accident from a second phone |
| Unbuilt swing items: Firestore hazard map, ADK orchestration, A2A agent, on-device sign OCR | Never built; to be cut loudly on one slide rather than left as ✅ in the ownership table |
| Broader packaging: LICENSE file, cost/BOM slide, "what we cut and why" slide | Not selected as MVP. The minimal claim-deletion in item 4 still ships |
| Human validation and a backup demo video | Not selected as MVP. **Flagged** — see Risks R1, R2 |
| Night, rain, glare; power and thermal budget; detection recall/mAP | No design consideration exists anywhere; out of reach this weekend. Scope honestly in the pitch as a stated boundary |

## Delivery Milestones

<!-- Business outcomes, not engineering tasks. /plan turns each into a plan. -->

| # | Milestone | Outcome | Status | Plan |
|---|---|---|---|---|
| 1 | Green trunk | The flagship steering feature produces cues at all, on a branch whose suites pass, and a failed depth path can no longer report itself healthy | pending | — |
| 2 | The cue is trustworthy | The steering cue points at a passable side, holds between frames, and stops saturating in ordinary corridors — including when metric scale is unrecoverable | pending | — |
| 3 | Failure is audible | The wearer learns within seconds, in speech, whenever the device has stopped seeing; no state reports "connected" while nothing is arriving | pending | — |
| 4 | The pitch is true and pointed | Every claim on the pitch surface is supported by the tree, and the lead claim is the one Shepherd cannot answer: awareness behind and beside the wearer, with identity and closing speed | pending | — |

## Open Questions

- [ ] Does the cloud L4 stay on the demo path? It was **not** marked out of scope, but the deployed
      container provably cannot run the depth head (entrypoint exports only the detector; the image
      never carries the depth sources or checkpoints; the configured device string is invalid). If it
      stays, milestone 1 must cover it; if it does not, the laptop-local path is already wired and
      the L4 becomes a slide with real measured numbers on it.
- [ ] What is the single demo sentence, verbatim? Draft: *"A white cane tells you what your tip
      touches; we tell you about the cyclist behind you."* Needs the team's agreement before the deck.
- [ ] Do we credit Shepherd explicitly in the pitch? Our own `docs/shepherd-steering-plan.md` already
      does. Claiming the port openly is likely stronger than letting a judge discover it.
- [ ] What is the wearer-reachable way to stop the aid? An outward-facing chest touchscreen currently
      stops everything on a 2-second press anywhere, with no spoken confirmation — a judge will poke
      the glowing orb.
- [ ] Which rung do we walk at, and is the TTL derived from it or fixed? Determines whether the cue
      feels continuous.

## Risks

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| **R1 — "Has a blind person used this?"** has no answer, and human validation was scoped out. It is the question an assistive panel always asks | High | High | Cheapest available answer is a filmed blindfolded identification trial; ~45 min, protocol already written in `step-10-brief.md`. Recommend re-admitting it |
| **R2 — No backup video and no rehearsed walk.** Step 25 has never been performed by a human; a live demo on venue wifi with no fallback is a coin flip | High | High | Record a backup on the first clean run. Recommend re-admitting |
| R3 — Fixes land but are never walked, so a regression appears on stage | Medium | High | Treat milestone 2 and 3 as unproven until one recorded end-to-end walk exists |
| R4 — The L4 is switched to under latency pressure and silently zeroes steering, with no alarm on any path the bridge watches | Medium | Critical | Resolve the open question above before rehearsal; if the L4 stays, it must fail loudly |
| R5 — A stranger or a teammate's second phone connects and evicts the wearer's link, killing the aid silently | Medium | High | Out of scope by decision; know it is reachable by accident and keep a second app instance off the venue network |
| R6 — Metric scale is confidently wrong (a mount-height or tilt error produces a clean fit residual) and the cue is authoritative anyway | Medium | High | Publish the unanchored state on the wire, never state metres from the ring, and say the limitation out loud before a judge finds it |
| R7 — Doc drift: the runbook, README and step briefs disagree on test counts, step numbering and what has run on hardware | High | Medium | Milestone 4 covers the claim surface; full doc reconciliation is deferred |

---

## Appendix — competitive position (evidence for the lead claim)

Rows where we are ahead and it is structural, not a tuning gap:

| Capability | Ours | Shepherd | Why it is structural |
|---|---|---|---|
| Angular coverage | 360°, six 60° crops plus a nadir crop; rear crop deliberately seam-free | One forward ARKit cone, analysed over the middle 30% of the frame | Their sensor is not pointed at the thing. No model or update changes that |
| Hazard identity and closing speed | ByteTrack IDs, closing speed, range bands to 15 m | Person *rectangles* at ~2 Hz, no IDs, no velocity, capped at 4.0 m | Moving-obstacle prediction is on their future-work list |
| Grounded scene Q&A | VLM over bearing-tagged dewarped crops, never the raw equirect | Hosted voice agent with sensor text injected | Ours is visual evidence with geometry attached |

Rows where they beat us and we should not fight: physical actuation, true LiDAR metres, ~$50 BOM
with CAD, offline operation, loop rate, and wearability. Our engineering rigour — a frozen contract
enforced in three places, 556 Swift tests, an AST test proving the cloud never reads a clock — is the
best work in the repo and will earn zero judge points. Shepherd has no tests and won three prizes.

*Full audit: 46 agents, 69 findings raised, 36 material, 33 surviving adversarial refutation.*
