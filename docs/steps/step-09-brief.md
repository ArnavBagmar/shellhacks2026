# Task 9 brief — Feedback manager gauntlet

Extracted from docs/orchestration-runbook.md (Step 9), docs/camera-sync-design.md §3.4, §4.4, §4.5, §4.7, docs/build-plan.md F4, plus controller rulings and the task-2 architect hazards. This file is the single source of requirements; exact values are binding.

## A. Runbook step text (verbatim)

## Step 9 — Feedback manager gauntlet

**Intent:** The consumer, and the most demo-visible code in the project. Starts hour 1.
**Chain rationale:** `architect` because gauntlet *order* is the design — dedup before refractory
before urgency is load-bearing and hard to re-sequence later. Deliberately no
`silent-failure-hunter`: this component drops events by design, so it would flag intended behaviour.

```bash
/everything-claude-code:orchestrate custom "ecc:architect,ecc:tdd-guide,ecc:swift-reviewer" "[Plan: docs/camera-sync-design.md#step-9] P3 lane, hour 1, built against the golden fixture with no camera and no cloud. Implement the §4.7 gauntlet in order: validate, staleness gate (now minus t_capture over ttl means drop and count), dedup (within 10° and 300 ms from a different source, preferring the phone when it has healthy metric range inside 15 m), refractory suppression keyed on track_id, on-device urgency, then a priority queue with preemption. TTLs come from §4.4. Acceptance: the fixture suite passes including malformed events; a flood of one track_id yields one announcement; urgency 1 produces haptic only and never speech"
```

---


## B. Spec: camera-sync-design.md §3.4 (verbatim)

### 3.4 Automatic fallback

Not a keystroke — a hall with a thousand people on 5G is a genuinely saturated uplink, and a manual
switch will not get flipped in time on stage.

```python
# Step DOWN a rung when:  p95 round-trip > 500 ms over last 10 frames
# Flip to LOCAL when:     3 consecutive timeouts
#                     OR  already on rung 4 and still failing
#                     OR  stale-drop rate crosses threshold (§4.4)
# Recover:                step up one rung after 30 s healthy
# User-visible:           nothing. Silent, logged.
```


## C. Spec: camera-sync-design.md §4.4, §4.5, §4.7 (verbatim)

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


## D. Spec: build-plan.md F4 (verbatim)

### F4 · Feedback manager — MVP, P3

The consumer. Buildable on hour 1 from the golden fixture with no camera, laptop, or cloud.

- **Hardware** — phone only
- **Libraries** — pure Swift. `Codable` structs against `contracts/awareness_event.schema.json`
- **Computation** — every event runs the gauntlet in order: **validate** (schema, enum,
  bearing ∈ (−180,180], ttl>0) → **staleness gate** (`now − t_capture > ttl` → drop, bump the
  counter) → **dedup** (within 10° and 300 ms from a different source; prefer the phone when it
  has healthy metric range inside 15 m) → **refractory suppression** (keyed on `track_id` — this
  is the anti-flooding mechanism) → **urgency** (on-device, never on the wire) → **priority queue**
  (one announcement at a time, higher urgency preempts). TTC = `range_m / closing_mps`; without
  metric range, `range_band` substitutes
- **TTLs** — cyclist/scooter 500 ms · car 400 ms · pedestrian 1.5 s · static 8 s · scene 15 s
- **Urgency** — 3 immediate (TTC<2 s): burst + one word, refractory 0 · 2 act-soon (TTC 2–5 s or
  static <2 m): directional haptic + 2–3 words, refractory 2 s · 1 aware: **haptic only, no
  speech**, refractory 8 s · 0 ambient: log + map, never announced
- **Cloud / external** — none. Drop counts feed the F8 fallback trigger: one mechanism, two jobs

---


## E. What earlier steps built (use, do not re-implement)
- Step 2: `ios/CaneKit` target `CaneContracts` — `AwarenessEvent` (11 `let` fields, camelCase names `tCaptureMs`, `ttlMs`, `bearingDeg`, `rangeM`, `rangeBand`, `closingMps`, `trackId`, `cls`, `source`, `confidence`, `schema`), `AwarenessEvent.decode(Data) throws`, `AwarenessEventError`, enums `Source`, `ObjectClass`, `RangeBand`. The golden fixture at `contracts/fixtures/awareness_events.golden.json` (cases with `expect`, plus `malformed_payloads`). Check the real names in `ios/CaneKit/Sources/CaneContracts/` before coding. Validation (gate 1) IS `AwarenessEvent.decode` — do not write a second validator.
- Step 3: `CaneTransport` delivers decoded `AwarenessEvent`s on an `AsyncStream`. The feedback manager consumes events; it does not know about sockets.

## F. Controller rulings — binding (the architect may amend; amendments land in task-9-design.md)

### F1. Placement and shape
- New target `CaneFeedback` in `ios/CaneKit` (depends on `CaneContracts` only). Pure Swift + Foundation; no UIKit/CoreHaptics/AVFoundation — those are step 10 behind a protocol. Tests run on macOS with `swift test`.
- Injected clock: every entry point takes `now: Int64` (phone-clock ms) or the manager is constructed with `now: () -> Int64`. No `Date()` inside the gauntlet.
- Immutability: the gauntlet state is a `struct FeedbackState`; each stage is a pure function `(FeedbackState, input) -> (FeedbackState, StageOutcome)`; `FeedbackManager` (final class) holds the current state and replaces it wholesale. No stage mutates shared collections in place.
- One file per stage: `Validation.swift` (wraps decode + future-dating), `StalenessGate.swift`, `DedupGate.swift`, `RefractoryGate.swift`, `UrgencyPolicy.swift`, `PriorityQueue.swift`, plus `FeedbackManager.swift` (wiring), `FeedbackState.swift`, `FeedbackCounters.swift`, `TTLTable.swift`, `Announcement.swift`, `AnnouncementSink.swift`. Keep each under ~200 lines.

### F2. Gauntlet order and exact rules (§4.7, with the task-2 architect's additions)
1. **Validate** — `AwarenessEvent.decode`. Failure → drop, `counters.invalidRejected += 1`, log. Additionally, `tCaptureMs > now + FUTURE_TOLERANCE_MS (250)` → drop, `counters.futureDated += 1` (a *separate* counter that does NOT feed the fallback signal — a bad clock offset is not fixed by switching detectors).
2. **Staleness gate** — effective TTL = `min(event.ttlMs, TTLTable.ttlMs(for: cls))`. `now − tCaptureMs > ttl` → drop, `counters.staleDrops += 1`. TTL table (ms): person 1500 · cyclist 500 · scooter 500 · car 400 · bus 400 · pole/bollard/wall/parked_car/door/overhang 8000 · unknown_obstacle 1500 (controller ruling: bus like car; unknown treated as possibly moving). Scene descriptions (15 s) are not events and never enter the gauntlet.
3. **Dedup gate** — another *accepted* event within `DEDUP_BEARING_DEG = 10` and `DEDUP_WINDOW_MS = 300`: (a) from a **different source**: keep the phone's when the phone event has healthy metric range inside 15 m (`source == .phone && rangeM != nil && rangeM! <= 15`), otherwise keep the X4's; the loser is dropped and counted (`counters.deduped`). (b) **Same X4 source, different trackId** (seam duplicate from adjacent crops, hazard 6): keep the earlier one. Bearing + time proximity only, no data association. Define "keep the phone's" precisely: if the incoming event wins, it proceeds AND the earlier loser (if not yet announced) is removed from the queue; if the incoming loses, it is dropped.
4. **Refractory suppression** — key is the pair `(source, trackId)`. If this key was announced within `refractory(lastUrgency)` and the new urgency is not strictly higher → drop, `counters.refractorySuppressed += 1`. Refractory by urgency: 3 → 0 ms (always speaks), 2 → 2000 ms, 1 → 8000 ms, 0 → never announced. **Failover window (hazard 4):** when the first event from `x4_local` arrives after events from `x4_cloud` (or vice versa), apply a `FAILOVER_SUPPRESS_MS = 2000` suppression to urgency ≤ 2 events from the new source whose `(cls, bearing within 10°)` matches an announcement made in the last 8 s from the old source.
5. **Urgency** — on-device only. TTC = `rangeM / closingMps` when `rangeM != nil && closingMps != nil && closingMps > 0`. Rules: TTC < 2 s → **3**; TTC 2–5 s → **2**; static class (`pole, bollard, wall, parked_car, door, overhang`) with `rangeM < 2` → **2**; with bands only: `near` + `closingMps > 3` → **3**; `near` + `closingMps > 0` → **2**; `mid` + `closingMps > 3` → **2**; otherwise moving class in `near`/`mid` (or metric range ≤ 15) → **1**; everything else → **0**. **Hysteresis:** per `(source, trackId)`, urgency may drop by one level only when the driving metric crosses the threshold by a margin (`TTC_HYSTERESIS_S = 0.5`, `RANGE_HYSTERESIS_M = 0.5`); upgrades are immediate.
6. **Priority queue** — one announcement at a time. Ordered by urgency desc, then `tCaptureMs` asc. A higher-urgency announcement **preempts** one in progress (`sink.cancelCurrent()` then `sink.present(next)`). Urgency 3 → `haptic: .burst`, `speech: .stop` (one word). Urgency 2 → `haptic: .directional(bearingDeg)`, `speech: .short(cls, bearing, range)`. Urgency 1 → `haptic: .directional(bearingDeg)`, `speech: nil` (NEVER speech). Urgency 0 → not queued; logged and forwarded to an `AmbientSink` (hazard-map hook, may be a no-op).
- `Announcement` is **structured** (urgency, cls, bearingDeg, rangeM/rangeBand, closingMps, haptic kind, speech kind, source, trackId, tCaptureMs). Step 10 renders words ("about two metres") and drives CoreHaptics/AVSpeech; step 9 never builds strings.
- `AnnouncementSink` protocol: `present(_:) async` (returns when done), `cancelCurrent()`. Provide `RecordingSink` in the test target.
- **Health signal for step 8:** `FeedbackCounters` plus `staleDropRate(windowMs: 10_000) -> Double` = staleDrops / eventsReceived in the trailing window (keep per-event timestamps in the state, bounded). This is what the reserved `health` message will carry.
- All constants at the top of the stage file that uses them, named as above.

### F3. Tests (TDD — RED then GREEN, evidence in the report), target `CaneFeedbackTests`
- Golden fixture drive: every `valid` case passes gate 1; every `invalid` case and every `malformed_payloads` string is rejected, counted, and never traps.
- Future-dated event dropped and counted separately from stale.
- Staleness: a cyclist event 600 ms old is dropped; a pole event 6 s old passes; producer `ttlMs` 15000 on a cyclist is clamped to 500.
- Dedup: phone-healthy (3 m) vs x4 within 5° / 100 ms → phone wins in both arrival orders; phone-unanchored vs x4 → x4 wins; same-source x4 seam duplicate → earlier kept; events 12° apart or 400 ms apart are both kept.
- Refractory flood: 30 x4 events, same `(source, trackId)`, urgency 2, over 1 s → exactly one announcement; a later urgency-3 event on the same track preempts immediately (refractory 0). Same `trackId` on two sources → two announcements.
- Failover window: cloud announces a cyclist at 170°; local then reports it at 172° within 2 s → suppressed.
- Urgency table: one test per rule row above, plus hysteresis (TTC 1.9 → 3; then 2.2 → still 3; 2.6 → 2).
- Priority queue: urgency 1 yields `speech == nil`; urgency 3 preempts an in-progress urgency 2 (RecordingSink records cancel then present); two urgency-2 announcements play in `tCaptureMs` order.
- Counters and `staleDropRate` computed correctly over a window.
- Acceptance from the runbook: fixture suite passes including malformed events; a flood of one track_id yields one announcement; urgency 1 produces haptic only and never speech.

### F4. Out of scope
CoreHaptics, AVSpeechSynthesizer, stereo panning, string rendering (step 10); voice, navigation (11, 12); the `health` message transport (step 8/3).

## G. Global constraints (verbatim)
- "The feedback manager drops. It never announces late."
- "Refractory suppression — keyed on track_id — this is the anti-flooding mechanism" (refined by task 2 to the pair (source, track_id)).
- "urgency 1 produces haptic only and never speech"; "Urgency thresholds use bands with hysteresis so a noisy estimate cannot flip urgency back and forth."
- Commit format `<type>: <description>`; no attribution trailers. Immutable data; small files; explicit error handling; constants not magic numbers.

## H. Architect amendments — ACCEPTED and BINDING
Read `task-9-design.md` (same directory) §2 A1–A17 in full before writing any code. They are binding; where they conflict with section F above, the amendment wins. In particular: the gauntlet order is validate → staleness → exact-duplicate → dedup → **urgency → refractory** → enqueue → pump (with re-gate at dequeue); no `FAILOVER_SUPPRESS_MS` (replaced by the spatial refractory table in A6); the urgency function is the total function in A7 with confidence gating (A8) and announced-anchored hysteresis (A9); the queue is keyed and bounded with a 2500 ms present timeout (A11); `Announcement` per A12 with no word chosen; `FeedbackManager` is an actor with two `ingest` entry points (A13, A16); counters and the 10-bucket stale-drop ring per A14; every collection bounded (A15); tests per F3 as rewritten by A17. Add `AngularMath.swift`, `Effect.swift`, `UrgencyDecision.swift`, `TrackRecord.swift`, `RecentAnnouncements.swift`, `RateRing.swift` to the F1 file list as needed; keep each file under ~200 lines.
Commit in at least three commits: (1) `feat: CaneFeedback stages — validation, staleness, dedup, angular math`, (2) `feat: CaneFeedback urgency, hysteresis, refractory`, (3) `feat: CaneFeedback queue, pump, manager actor + walk timeline tests`.

## I. Controller ruling on CaneContracts (small prerequisite, do it as your first commit)
`AwarenessEvent` currently has no public initializer other than `init(from decoder:)`, so no other target can construct an event in memory. Add to `CaneContracts` a `public init(schema: Int = 1, source: Source, trackId: String, tCaptureMs: Int64, ttlMs: Int, cls: ObjectClass, confidence: Double, bearingDeg: Double, rangeM: Double?, rangeBand: RangeBand?, closingMps: Double?) throws` that runs the **same** bound/conditional validation the decoder runs (refactor the checks into one shared internal function used by both paths — no duplicated bound logic) and throws `AwarenessEventError`. Add two tests in `CaneContractsTests`: a valid memberwise init round-trips through `encoded()`/`decode`, and an X4 init with `rangeM` set throws. Keep the existing 16 tests green. Commit as `feat: public validating initializer for AwarenessEvent`.
