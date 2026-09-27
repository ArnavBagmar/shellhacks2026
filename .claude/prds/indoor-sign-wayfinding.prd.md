# Indoor wayfinding from the building's own signs

**Status:** DRAFT — requirements only. Implementation planning pending via `/plan`.
**Created:** 2026-09-27 · **Horizon:** the remaining hours before ShellHacks judging (today)
**Parent:** `.claude/prds/win-shellhacks.prd.md` — which lists on-device sign OCR as an *unbuilt
swing item, explicitly out of scope*. This PRD re-admits a different version of it: sign reading on
the **360° crops**, not the phone.

> **Scope decision (user, 2026-09-27):** scoped to **today's judging demo**, not the post-hackathon
> product. Where a finding matters beyond the deadline it is recorded under Risks rather than dropped.

---

## Problem

A white cane tells you what its tip touches, and the build so far tells you what is moving around
you. Neither tells you *where anything is*. Indoors there is no GPS, no Routes API, and for the
wearer no floorplan — so the ordinary question a person actually has ("where is the restroom", "which
way is room 100") has no answer on this device at all. The building has already answered it, in print,
on signs at eye level in every corridor — and we are the only rig at the table with cameras pointed
at all of them at once, including the ones the wearer has already walked past.

The cost of leaving it unsolved is that the demo can only ever be defensive. Every beat is about
avoiding something. A judge who asks "so it stops him hitting things — can it take him anywhere?"
currently gets no.

## Evidence

- **Assumption — needs validation via a timed trial with a blind or blindfolded participant.** The
  user's own characterisation: this is a judge-appeal bet, not an evidenced user need. No blind user
  or O&M professional has been consulted on whether sign-relay is how they would want to be guided,
  or whether a remembered bearing is usable at all without a step count. This inherits R1 of the
  parent PRD and does not resolve it.
- **The frozen wire already anticipated this feature, and needs no breaking change.**
  `contracts/transport_envelope.schema.json:232` ships an `answer` frame with
  `status: found | not_found | described | unavailable`, `bearing_deg`, `detail`, `confidence` — and
  its own example payload is literally `"subject": "restroom sign", "bearing_deg": 72.5,
  "detail": "past the elevators"` (line 342). Golden fixtures for it exist. Verified 2026-09-27.
- **The one-shot version is already built; the standing version is not.** Step 20 ("ask the 360
  view" via Gemini) ships per `README.md:55`, and `docs/steps/step-20-brief.md:141` already
  rehearses "is there a bathroom around?" with a taped-up sign. What does not exist: any memory of a
  sign once it leaves view, and any search that survives the tick it was asked in.
- **There is no persistence layer of any kind.** The Firestore hazard map is listed as never built
  in the parent PRD's out-of-scope table.
- **A documented ruling currently says this is impossible.** `docs/device-responsibilities.md:100`
  and `docs/steps/step-06-brief.md:146` both rule that OCR is phone-only because "fisheye crops
  would wreck it". The X4 path dewarps to bearing-tagged rectilinear crops before inference, which is
  why this feature is reachable — but the doc says otherwise and a judge can read it.

## Users

- **Primary: a ShellHacks judge at the table with ~4 minutes**, who has just been shown three
  hazard-avoidance beats and wants to know whether the device can do anything *for* the wearer rather
  than only warn them. Sighted, technical, will ask where the map came from.
- **Depicted: a blind or low-vision pedestrian** in an unfamiliar building — a conference centre, a
  clinic, a campus — who knows the room number they were given and nothing else. They are the subject
  of the demo and the arbiter of whether the guidance is honest. Nobody in this population has used
  this build.
- **Not for:** anyone relying on it to actually reach a destination today. It cannot reason about
  floors, it holds no map between sessions, and a remembered bearing is only as good as the wearer's
  path since it was recorded.

## Hypothesis

We believe **turning signage the 360° ring reads into remembered, askable places** will **give the
wearer a destination rather than only a warning** for **a blind pedestrian in an unfamiliar building,
and for the judge deciding whether this is an avoidance gadget or a navigation aid**.

We'll know we're right when **the rig, started knowing nothing about the judging building, is asked
for a restroom, and guides the wearer to within arm's reach of the door from signs alone — on three
of three rehearsals — and when asked about a sign already walked past, answers "behind you, on your
left."**

## Success Metrics

| Metric | Target | How measured |
|---|---|---|
| Cold-start find-and-arrive | 3 of 3 rehearsals: wearer reaches arm's reach of the restroom door | Timed walk in the judging building, rig started fresh, wearer takes only spoken guidance |
| Seeded building knowledge on the demo path | **0** landmark entries — every place came from a sign read live | Grep audit of the tree and config for hardcoded rooms/restrooms before rehearsal |
| Recall of a sign no longer in view | Correct side (left/right/behind) on 5 of 5 asks, ≥ 30 s after passing it | Walk protocol: pass a sign, keep walking, then ask |
| Time from question to first spoken response | ≤ 3 s, and **never silence** — either a bearing or "not yet, keep walking" | Stopwatch on the recorded walk; inherits the silence-is-failure rule from the parent PRD |
| Reassurance cadence while searching | The wearer is told the search is still live at least every 10 s | Same walk, spoken-cue log |
| Asserted signs that were not there | **0** — returns silence or "not yet" rather than a guess | Review the recorded walk transcript against the video |
| Wrong-direction guidance | 0 cues that send the wearer away from a target that was in view | Same walk review |

## Scope

**MVP — the minimum that tests the hypothesis**

1. **Signs read off the 360° ring become facts with a bearing.** Sign text recognised on the
   dewarped, bearing-tagged crops — so signs beside and behind the wearer count, which is the whole
   reason to do it here instead of on the phone. The documented "OCR is phone-only" ruling is amended
   in the same change rather than left to contradict the code.
2. **The building accumulates as in-session memory.** A sign that has left view is still a place the
   system can name and point toward. Memory dies with the session — no storage layer, nothing to
   secure, nothing to leak.
3. **A destination question becomes a standing search, not a one-shot answer.** Asking for a restroom
   or a room number either answers from memory immediately, or starts a search that stays alive while
   the wearer walks, tells them so, and speaks the moment a matching sign enters any crop.
4. **Guidance continues until arrival.** The wearer is kept oriented toward the target as they move,
   not told a bearing once and abandoned.

**Discovered during implementation (2026-09-27)**

- **The outdoor half already existed.** `ios/CaneKit/Sources/CaneNavigation/` is a complete, tested
  Google Routes + Places walking-directions engine (26 files, 16 test files), with the app's only
  `CLLocationManager`, an API-key path and a `nav-cli` dev tool. "Outdoors uses Google Maps" needed
  no Maps work at all -- only a switch deciding who owns a destination.
- **No OCR engine was needed.** Gemini already reads signs (`scene/prompt.py`), so milestone 1 was
  most of the way there; the real work was memory, a search that survives, and the switch.
- **The frozen contract was not touched.** The standing search is the phone re-asking `find` while
  it walks, answered from the laptop's sign memory -- so no new frame type, no schema change.

**Decisions taken (user, 2026-09-27)**

- **Nothing is pre-seeded.** The user's opening brief floated hardcoding the judging building, since
  there is only one. That was **rejected in favour of learning live only** — every place comes from a
  sign the cameras actually read this session. This is the stronger claim and the weaker demo; the
  trade is recorded as R1 below.
- **The X4 crops are the only sign source.** Phone-side Vision OCR (step 13) stays unbuilt.
- **Not-found means directed exploration**, not a shrug: "not yet — keep walking, I'll tell you when
  I see one."

**Out of scope**

| Item | Why |
|---|---|
| Phone-side sign reading (step 13, `VNRecognizeTextRequest`) | User decision. All sign reading comes from the 360° crops; the forward-cone OCR path stays unbuilt |
| Cross-session or cross-device memory (the Firestore revival) | In-session only by decision. Keeps the build small and adds no stored-imagery surface |
| Any pre-seeded landmark list, venue map or floorplan | Rejected decision above — it would make the perception claim false |
| Metric distance to a target | Inherited hard constraint: the parent PRD forbids stating metres from the unanchored ring (R6). Side and bearing only |
| Human validation of the guidance vocabulary | Not admitted here either. Flagged — see R2 |

## Delivery Milestones

<!-- Business outcomes, not engineering tasks. /plan turns each into a plan. -->
<!-- Status: pending | in-progress | complete -->

| # | Milestone | Outcome | Status | Plan |
|---|---|---|---|---|
| 1 | Signs become facts | Ask "what does that sign say?" facing any direction — including behind — and hear the text and which way it is. Nothing is remembered yet | built, unwalked | [plan](~/.claude/plans/claude-prds-indoor-sign-wayfinding-prd-abstract-wigderson.md) |
| 2 | The building becomes memory | Walk past a sign, keep going, ask about it 30 s later, and hear "behind you, on your left" | built, unwalked | [plan](~/.claude/plans/claude-prds-indoor-sign-wayfinding-prd-abstract-wigderson.md) |
| 3 | A question becomes a search | Ask for a restroom before any restroom sign has been seen, and be told to keep walking — then be told the moment one appears, unprompted | built, unwalked | [plan](~/.claude/plans/claude-prds-indoor-sign-wayfinding-prd-abstract-wigderson.md) |
| 4 | The search becomes arrival | Cold-start walk in the judging building ends at arm's reach of the door, guided the whole way, rehearsed three times | pending | — |

## Open Questions

- [ ] **Has anyone looked at the judging building's actual signage?** Restrooms are often a pictogram
      with no word, mounted high, backlit or behind glass. If the venue's restroom signs carry no
      readable text, MVP item 3 cannot fire and the cold-start demo has nothing to find. This is the
      one question that can invalidate the whole PRD, and it is answerable in ten minutes with a
      phone walk-through.
- [ ] **How does a remembered bearing survive the wearer moving?** There is no odometry in this
      system. A sign recorded at "72° right" is wrong as soon as the wearer turns a corner. Options —
      speak only coarse, self-correcting relations ("behind you"), re-confirm by re-seeing the sign,
      or admit staleness out loud — have materially different scope. Unresolved, and it gates
      milestones 2 and 4.
- [ ] **Who owns the speech channel?** Hazard warnings, the steering cue and now wayfinding all speak
      to one wearer. A wayfinding sentence that masks a hazard cue is a safety regression, not a
      feature. Priority rule needed before milestone 3.
- [ ] **Floors.** Not declared out of scope, but nothing in the system can know the restroom is
      upstairs. Decide: handle stair/elevator signs as landmarks, or state the boundary out loud in
      the pitch before a judge finds it.
- [ ] **Does "arm's reach of the door" require a range claim we are forbidden to make?** The success
      bar says arrival; the inherited constraint says no metres from the ring. Reconcile — most likely
      by defining arrival as the sign filling the crop, not as a distance.
- [ ] **Where does this sit in the 4-minute script**, given the parent PRD already owns the walk-up-
      behind beat and a failure-is-audible beat?

## Risks

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| **R1 — Cold start finds nothing.** With zero seeding, if no legible sign is in view the entire demo output is "keep walking", which reads as broken | High | High | Survey the venue signage *before* rehearsal (see first open question). Fallback is one decision away and uses the same code path: pre-walk the building with the rig and keep the memory it built — still nothing hand-written |
| **R2 — This is a fifth milestone opened on judging day while the parent PRD's milestone 1 is still pending** — the flagship steering feature currently emits nothing at all. Building wayfinding first risks a table demo where the safety feature is silent and the navigation feature is unrehearsed | High | Critical | Sequence explicitly: do not start milestone 1 here until steering produces cues. If time forces a choice, a working warning beats a charming one |
| **R3 — Remembered bearings go confidently stale** and send the wearer the wrong way with full confidence — the same failure class as the parent PRD's R6 | High | High | Resolve the odometry question before milestone 2 ships; prefer coarse self-correcting relations and degrade to "I saw one, I've lost track of where" rather than asserting a stale angle |
| **R4 — A shipped doc says this cannot work.** Two files rule OCR phone-only on fisheye grounds; a judge or teammate reading them concludes the 360° sign path is fiction | Medium | Medium | Amend the ruling in the same change as milestone 1 — the crops are dewarped, and saying so is the interesting technical point |
| **R5 — Misread text sends the wearer to the wrong room.** "ROOM 1OO", a fire-escape floor plan read as directions, a directory board read as a location | Medium | High | Require a confidence floor and prefer silence to a guess; never speak a room number the system is not sure of |
| **R6 — Round-trip latency outlives the situation.** Sign reading on the cloud path means the answer can land seconds after the wearer has turned | Medium | Medium | Hold the target as a standing search rather than a one-shot reply, so a late answer is still a live one; measure question-to-speech on the recorded walk |
| **R7 — Bystander imagery and stored text.** Sign reading photographs whatever else is in the corridor, and in-session memory now holds recognised text | Low (today) | Medium | In-session-only memory is the mitigation. Legal/privacy remains deferred per the parent PRD |

---

## Appendix — why the 360° ring is the right sensor for this

| | Ours | A phone-only device |
|---|---|---|
| Signs in view | Every bearing at once; a corridor's worth of signage per frame | Whatever the wearer happens to be pointed at |
| Signs already passed | Rear crop reads them as the wearer walks by, so they can be recalled later | Never seen; the wearer would have to turn around and sweep |
| The demo beat | "The restroom is behind you, on your left" — recalled from a sign nobody aimed at | Structurally unavailable |

This is the same structural argument as the parent PRD's lead claim, applied to text instead of
hazards: the advantage is not a better model, it is that the sensor is pointed at the thing.
