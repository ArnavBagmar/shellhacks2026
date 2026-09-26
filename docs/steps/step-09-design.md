# Task 9 design review — Feedback manager gauntlet (ecc:architect, opus)

Controller ruling: **ACCEPTED, all seventeen amendments A1–A17 are binding.** Where an amendment conflicts with section F of task-9-brief.md, the amendment wins. The runbook's literal "refractory before urgency" order is overridden by A1 because §4.7's own refractory predicate ("urgency has not escalated") is a function of the new urgency; dedup still precedes refractory, which is the load-bearing constraint.

## 1. Verdict — ACCEPT WITH AMENDMENTS

Ratified from F: `CaneFeedback` as a `CaneContracts`-only target (no UIKit/CoreHaptics); refractory key = `(source, trackId)`; structured `Announcement`; staleness BEFORE dedup (a stale phone event with healthy range would otherwise win dedup, suppress a fresh X4 event, then be dropped — total silence).

Four defect classes in F: (1) refractory needs the new urgency, which F computes later — the gauntlet as written cannot run; (2) the urgency table is not total and scores 0 on a pole ahead with nil `closingMps` (the normal phone static) and on a far, fast-closing cyclist; (3) dedup-loser-already-announced, seam duplicates and the failover window are one mechanism — a bearing-keyed memory of recent announcements; (4) nothing re-checks staleness when a queued announcement actually plays, so a 500 ms-TTL cyclist behind a 1.5 s utterance is announced late, structurally.

Two facts to keep in mind: `DEDUP_WINDOW_MS = 300` cannot cover the X4 alone (it runs at 2–3 fps, so gate 4 is the fast path and the spatial refractory is the guarantee); the cyclist TTL (500 ms) is below the cloud path's p95, so cyclist silence on a saturated uplink is §4.4 working and must not be "fixed" by loosening the TTL.

## 2. Amendments (binding)

### A1 — Gauntlet order; two stages inserted
Every event runs this gauntlet in order. **Stage 5 (urgency) runs before stage 6 (refractory).**
1. **Validate** — decode + future-dating (`tCaptureMs > now + FUTURE_TOLERANCE_MS (250)` → drop, `futureDated`).
2. **Staleness gate** — effective TTL = `min(event.ttlMs, TTLTable.ttlMs(for: cls))`; drop and `staleDrops`.
3. **Exact-duplicate guard** — an event whose `(source, trackId, tCaptureMs)` triple was already processed within `DEDUP_RETENTION_MS` is dropped, `duplicateExact += 1` (transport reconnects can replay).
4. **Dedup gate** — cross-source total order + same-source seam rule (A3–A5).
5. **Urgency** — pure function `(AwarenessEvent, TrackRecord?) -> UrgencyDecision` (A7–A9). Reads state, never writes it.
6. **Refractory** — per-key + spatial + handoff, all against `UrgencyDecision.urgency` (A6, A10).
7. **Enqueue** — urgency 0 → `AmbientSink`; otherwise insert-or-replace by key (A11).
8. **Pump** — dequeue, **re-gate staleness at dequeue**, throttle, preempt, present (A11).
Data per stage: (1) `now`; (2) `now` + `TTLTable` + read-only `tracks`; (3) `recentAccepted`; (4) `recentAccepted` + `queue`; (5) read-only `tracks`; (6) `tracks` + `recentAnnouncements`; (7) `queue`; (8) `now` + `queue` + `tracks` + `recentAnnouncements` + sink.

### A2 — Angular arithmetic in one file
Add `AngularMath.swift`. All bearing comparisons go through `AngularMath.delta(_ a: Double, _ b: Double) -> Double`, the signed difference in `(-180, 180]`: `((a - b).truncatingRemainder(dividingBy: 360) + 540).truncatingRemainder(dividingBy: 360) - 180`. No stage subtracts bearings directly. `179.0` and `-178.0` are 3° apart.

### A3 — Dedup matches on `tCaptureMs`, retains on arrival
The 10°/300 ms proximity test compares `tCaptureMs` to `tCaptureMs` (`abs(a.tCaptureMs - b.tCaptureMs) <= DEDUP_WINDOW_MS (300)`), never arrival time. Entries are retained in `state.recentAccepted` while `now - entry.arrivalMs <= DEDUP_RETENTION_MS (1200)`, because the X4's version of an object arrives 200–500 ms after the phone's.

### A4 — Dedup requires class agreement; source preference is a total order
Two accepted events are dedup candidates iff `abs(AngularMath.delta(...)) <= DEDUP_BEARING_DEG (10)`, `abs(Δ tCaptureMs) <= DEDUP_WINDOW_MS`, **and their `cls` agree** — with one directional exception: an `unknown_obstacle` and a classified event at the same bearing/time are candidates and the classified one always wins (`unknown_obstacle` may lose, never win).
Among candidates from different sources the winner is the maximum of this **total order** (independent of arrival order):
`phone with rangeM != nil && rangeM <= TRUST_RANGE_M (15)` > `x4_local` > `x4_cloud` > `phone with no trustworthy metric range`.
The loser is dropped, `deduped += 1`. If the incoming event wins and the loser is queued and not yet presented, remove the loser from the queue. If the loser has already been presented, nothing is evicted — the spatial refractory (A6) decides.

### A5 — Same-source dedup only near a crop seam
**(b) Same source, different `trackId`** — applies only when both bearings lie within `SEAM_HALF_WIDTH_DEG = 12` of the same member of `SEAM_BEARINGS_DEG = [-180, -150, -90, -30, 30, 90, 150, 180]`. Keep the event with the smaller `tCaptureMs`; tie-break on lexicographically smaller `trackId`. `dedupedSeam += 1`.

### A6 — No `FAILOVER_SUPPRESS_MS`; one spatial refractory that also covers the §4.5 handoff
`state.recentAnnouncements` is a bounded list (`MAX_ANNOUNCEMENT_RECORDS = 16`, pruned by age) of `{key, cls, bearingDeg, urgency, presentedAtMs}`, appended at presentation start. An event with urgency `u` is suppressed (`spatialSuppressed += 1`) if any record `r` with `r.cls == event.cls` satisfies any of:

| relation | window | angular tolerance | condition |
|---|---|---|---|
| same key `(source, trackId)` | `refractory(r.urgency)` | — | `u <= r.urgency` |
| different key, any source | `refractory(r.urgency)` | `SPATIAL_REFRACTORY_DEG = 10` | `u <= r.urgency` |
| `r` from an X4 source, event from `phone` | `HANDOFF_WINDOW_MS = 1500` | `HANDOFF_DEG = 30` | `u <= r.urgency` |
| any, when `u == 3` | `URGENCY3_REPEAT_MS = 1000` | 10° | always (repeat floor only) |

Urgency 3 is exempt from every duration window except its own 1000 ms repeat floor. The handoff row is directional (X4 → phone only) and never suppresses an escalation.

### A7 — Urgency is a total function
```
effectiveBand(e) = e.rangeBand ?? band(from: e.rangeM)   // near <=5, mid 5..15, far >15
                                 ?? .far                  // both nil: least urgent, counters.bandUnknown += 1
trusted(e)       = e.rangeM != nil && e.rangeM! <= TRUST_RANGE_M (15)

STATIC classes (pole, bollard, wall, parked_car, door, overhang) — TTC never applies:
  abs(bearing) > STATIC_REAR_DEG (90)            -> 0     // cannot be walked into
  trusted && rangeM <  STATIC_ACT_M   (2.0)      -> 2
  trusted && rangeM <= STATIC_AWARE_M (5.0)      -> 1
  !trusted && band == .near                      -> 1
  otherwise                                      -> 0

MOVING classes (person, cyclist, scooter, car, bus) and unknown_obstacle:
  closing != nil && closing < RECEDING_MPS (-0.5)             -> 0
  trusted && closing != nil && closing > 0:
      ttc = rangeM / closing
      ttc <  TTC_STOP_S (2.0)                                 -> 3
      ttc <= TTC_ACT_S  (5.0)                                 -> 2
      // else fall through to the band ladder
  fast = closing != nil && closing > FAST_CLOSING_MPS (3.0)
  approaching = closing != nil && closing > 0
  band == .near: fast -> 3 ; approaching -> 2 ; else -> 1
  band == .mid : fast -> 2 ; else -> 1
  band == .far : fast -> 1 ; else -> 0
```
The class switch is exhaustive over the 12-value enum with **no `default`**, in both `UrgencyPolicy` and `TTLTable`. Escalation check: pole 0 at 6 m, 1 at 4 m, 2 at 1.9 m; cyclist 1 at 20 m, 2 at 15 m, 3 at 5 m, 0 after passing.

### A8 — Confidence gates the output level
After the ladder and before hysteresis: `confidence < MIN_ANNOUNCE_CONFIDENCE (0.25)` → 0; `urgency == 3 && confidence < MIN_STOP_CONFIDENCE (0.60)` → 2; `urgency == 2 && confidence < MIN_SPEECH_CONFIDENCE (0.50)` → 1. Demote at most one level; never below 1 for a detection above the announce floor.

### A9 — Hysteresis anchored on the last announced decision
`UrgencyDecision = (urgency: Int, driver: DrivingMetric)` where `DrivingMetric = .ttc(Double) | .staticRange(Double) | .band(RangeBand)`. `TrackRecord` (keyed `(source, trackId)`, written **only at presentation start**) stores `{lastUrgency, lastDriver, presentedAtMs, lastSeenMs}`.
Upgrades are immediate. A **downgrade** (`raw < record.lastUrgency`) is accepted only if either: the driver kind is unchanged and crossed by the margin — `.ttc`: `new >= threshold + TTC_HYSTERESIS_S (0.5)`; `.staticRange`: `new >= threshold + RANGE_HYSTERESIS_M (0.5)`; `.band`: the band changed by a full step; or the driver kind changed and `now - record.presentedAtMs >= HYSTERESIS_DWELL_MS (1000)`. Otherwise announce at `record.lastUrgency` (held, `hysteresisHeld += 1`). Once accepted, a downgrade may drop more than one level. **Hysteresis never applies to a demotion to 0 produced by the receding rule, by `abs(bearing) > STATIC_REAR_DEG`, or by the staleness path.** Records with `now - lastSeenMs > TRACK_RECORD_TTL_MS (10_000)` are evicted.

### A10 — Refractory comparisons are strict; urgency 3 gets a repeat floor
Suppress iff `now - record.presentedAtMs < refractory(record.lastUrgency)` (**strict `<`**) **and** `newUrgency <= record.lastUrgency`. `refractory(3) = 0`, `refractory(2) = 2000`, `refractory(1) = 8000`; urgency 0 is never announced. Urgency 3 is floored: the same key, or any same-class record within 10°, may not re-present at urgency 3 within `URGENCY3_REPEAT_MS = 1000`.

### A11 — Priority queue: keyed, bounded, re-gated at dequeue, never replayed
The queue holds at most one pending item per `(source, trackId)`: an arriving item with an existing key replaces it if its urgency is higher or its `tCaptureMs` is newer. Order: urgency desc, then `tCaptureMs` asc. `MAX_QUEUE_DEPTH = 8`; on overflow evict the lowest-urgency, oldest item, `queueOverflow += 1` — an urgency-3 item is only ever evicted by another urgency-3 item.
**At dequeue**, before presenting, re-run the staleness test against the item's own `tCaptureMs` and clamped TTL with a fresh `now`. Expired → discard, `queueExpired += 1` (does NOT feed the §3.4 fallback) and dequeue the next.
Speech throttle: an item with urgency ≤ 2 is held if `now - lastSpeechAtMs < MIN_SPEECH_GAP_MS (1200)`. Urgency 3 is exempt.
Preemption requires **strictly** higher urgency. A preempted announcement is **dropped: never resumed, never re-queued, and its `TrackRecord` stands.**
Presentation is wrapped in a `Task` with `PRESENT_TIMEOUT_MS = 2500`: on timeout call `sink.cancelCurrent()`, abandon the task, `sinkTimeouts += 1`, continue. Order on preemption: `sink.cancelCurrent()` → `task.cancel()` → `await` the task → present the next. `TrackRecord` and `recentAnnouncements` are written at **presentation start**.

### A12 — `Announcement` shape
```swift
public struct Announcement: Equatable, Sendable {
  let id: UInt64                 // monotonic; correlates cancel with present
  let urgency: Int
  let cls: ObjectClass
  let bearingDeg: Double
  let range: RangeHint           // .aboutMetres(Int) | .band(RangeBand) | .unknown
  let closingMps: Double?
  let haptic: HapticKind         // .burst | .directional
  let speech: SpeechVerbosity?   // .oneWord | .brief ; nil == silent
  let source: Source
  let trackId: String
  let tCaptureMs: Int64
  let decidedAtMs: Int64         // phone clock at dequeue → §4.9 latency, free
  let expiresAtMs: Int64         // tCaptureMs + clamped TTL
}
```
`haptic` and `speech` are set by a **private** table inside the single initializer `init(urgency:event:decidedAtMs:...)`; an urgency-1 `Announcement` with non-nil `speech` is unrepresentable. Urgency 3 → `(.burst, .oneWord)`; 2 → `(.directional, .brief)`; 1 → `(.directional, nil)`. Step 9 never names the word. `range` carries the rounding policy as data (`.aboutMetres(Int)` = rounded whole metres).

### A13 — Sink protocols and concurrency
```swift
public protocol AnnouncementSink: Sendable {
  func present(_ a: Announcement) async     // returns when finished or cancelled; must not throw
  func cancelCurrent()                      // sync, non-blocking, idempotent, safe when idle
}
public protocol AmbientSink: Sendable {
  func record(_ event: AwarenessEvent, urgency: Int)   // sync, non-blocking
}
```
`FeedbackManager` is an **`actor`**; stage functions are `nonisolated` pure functions so tests stay synchronous. `FeedbackState` is `Equatable` with no closures. Every gauntlet entry point takes `now: Int64` explicitly; the manager is constructed with `clock: @Sendable () -> Int64` used only by the pump.

### A14 — Counters and the fallback signal
`FeedbackCounters` fields: `eventsReceived, invalidRejected, futureDated, staleDrops, staleDropsByClass, staleDropsByWouldBeUrgency, duplicateExact, deduped, dedupedSeam, refractorySuppressed, spatialSuppressed, hysteresisHeld, bandUnknown, ambientLogged, announced, preempted, queueOverflow, queueExpired, sinkTimeouts`.
**Exactly one** counter feeds the §3.4 fallback: `staleDrops`. The rate is a ring of 10 one-second buckets `{received, staleDropped}`: `staleDropRate(now) -> Double` (0 when received == 0) and `isStaleDropRateUnhealthy: Bool` = rate ≥ `STALE_DROP_RATE_THRESHOLD (0.2)` over at least 2 complete buckets. On a stale drop, still compute urgency and bump `staleDropsByWouldBeUrgency`.

### A15 — State bounds and exhaustive tables
`tracks` — `MAX_TRACK_RECORDS = 256`, evict oldest `lastSeenMs`, age-out at `TRACK_RECORD_TTL_MS = 10_000`; `recentAccepted` — age-out at `DEDUP_RETENTION_MS`; `recentAnnouncements` — 16; queue — 8; rate ring — 10 buckets. `TTLTable.ttlMs(for:)` and `UrgencyPolicy`'s class switch are exhaustive with no `default`.

### A16 — Two entry points; effects as intents
`FeedbackManager` exposes `ingest(_ data: Data, now: Int64) async` and `ingest(_ event: AwarenessEvent, now: Int64) async`. Stages return **intents** (`enum Effect { case cancelCurrent, present(Announcement), ambient(AwarenessEvent, Int) }`); only `FeedbackManager` performs them. No stage calls a sink.

### A17 — Test list changes
Add: bearing-wrap dedup (179 vs −178); dedup matched on `tCaptureMs` when arrivals are 450 ms apart; dequeue-time staleness with a busy sink; the full four-object walk timeline (pole ahead via phone, overtaking cyclist via X4 bands, crossing pedestrian X4-then-phone handoff, wearer at 1.4 m/s) asserting the exact announcement sequence; urgency 3 penetrating a full queue plus active refractory plus active spatial refractory; the static-pole ladder producing speech at <2 m; `unknown_obstacle`; confidence demotion; both range fields nil (no force-unwrap); exact-duplicate replay; bounded state after 10 000 events; a monotonicity property loop over the urgency table; sink timeout does not stall the queue; a preempted announcement is not replayed; two events ingested concurrently do not lose a `TrackRecord`.
Rewrite: the preemption test asserts `presented.map(\.urgency) == [2, 3]` and `cancelled.contains(id₂)`; the TTL-clamp test asserts the drop plus counter for a 600 ms-old cyclist carrying `ttlMs: 15000`; the "same `trackId` on two sources → two announcements" test places the events at clearly different bearings (0° and 120°); the failover test becomes a spatial-refractory test (different `trackId`, same class, 170°/172°) plus an urgency-3 variant asserting exactly one presentation.

## 3. The three failure classes (reviewer checklist)
*Announces late:* no dequeue re-gate → A11; unbounded queue replaying the past → A11; hysteresis holding urgency 2 on a receded object → A9 carve-out; `present` with no deadline → `expiresAtMs` + `PRESENT_TIMEOUT_MS`.
*Announces twice:* unwrapped bearing subtraction at ±180 → A2; the X4→phone handoff 24° apart → A6 handoff row; presented dedup loser → A6; cloud↔local failover → A6; seam duplicates → A5+A6; transport replay → A1 stage 3; the phone's `track_id` changing across a bucket → A6 contains, P1 fixes (H1).
*Silent on urgency 3:* cross-source dedup with no class guard → A4; static-in-path scoring 0 → A7; far + fast → A7; overflow evicting urgency 3 → A11; hung sink → timeout; `<=` in refractory → A10. One silence is correct: a cyclist event that took 520 ms to arrive is dropped — that is §4.4 working and the drop triggers §3.4.

## 4. Hazards for later steps (controller notes)
- **H1 (P1 / step 4, highest):** the phone's `cls` + bearing-bucket `track_id` re-keys a crossing pedestrian about once per second. Step 4 must do a sticky nearest-neighbour association over the previous frame's boxes and reuse the previous `track_id` when the match is within a few degrees and one band.
- **H2 (step 10):** word choice depends on bearing sector; "Stop" is wrong for an overtaking cyclist at 178°. Step 10 owns a table over `(cls, bearing sector)`.
- **H3 (step 10):** `present` must be fast and cancellable: budget speech ≤ 1.2 s, honour cancellation via `withTaskCancellationHandler` around the AVSpeech delegate continuation, return within ~50 ms of `cancelCurrent()`.
- **H4 (step 8):** read `isStaleDropRateUnhealthy` / `staleDropRate`; never sum `futureDated` or `queueExpired` into the trigger.
- **H5 (demo):** cyclist TTL 500 ms < cloud p95, so decide before the demo whether the cyclist segment starts on the local detector.
- **H6 (knobs):** `STATIC_ACT_M`, `MIN_SPEECH_GAP_MS`, `SPATIAL_REFRACTORY_DEG` are the three demo knobs, each one constant at the top of its stage file.
- **H7 (P1/P2):** phone and X4 detectors must share the 12-value class list; a classified-vs-classified disagreement announces twice (safe direction).
