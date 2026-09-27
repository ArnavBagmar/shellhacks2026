# Plan: Threat-Aware Hazard Detection

**Source PRD**: `.claude/prds/threat-aware-hazards.prd.md`
**Selected Milestone**: 1 — Trust baseline measured
**Complexity**: Medium

## Summary

Build the measuring instrument before changing any behaviour. Record the live
`AwarenessEvent` stream to disk during a real traffic-adjacent walk, then replay
that recording deterministically through the existing `FeedbackManager` and tally
what the phone *would* announce. The recording makes milestone 4's re-measurement
an A/B on byte-identical input instead of two incomparable walks.

Nothing in this milestone changes runtime behaviour. The recorder is a decorator
that forwards unchanged; the replay tool is offline.

## Key design decision: record-then-replay, not measure-live

A live walk cannot be re-run. Traffic density, vehicle speed, the wearer's pace
and the route all differ between takes, so a before/after across two live walks
measures the traffic, not the gate. Recording the event stream once and replaying
it through both the current and the gated feedback manager isolates the one
variable that matters.

Three consequences, all good:

- The phone is **not on the critical path**. `FeedbackManager` and its sinks are
  plain Swift libraries that build for macOS, so the tally runs on the laptop.
  The walk needs only the X4 + laptop with `--allow-unsynced`.
- Milestone 4 costs one command instead of a second field trip.
- The tally is reproducible, so a disputed number can be re-derived.

## Key design decision: no video annotation on the critical path

The PRD's metric table says false Stops are "reconciled against hand-annotated
video." That is slow and it is not necessary if the walk is designed so the
ground truth is known by construction:

| Take | Route property | Ground truth |
|---|---|---|
| **A** — traffic walk | Sidewalk beside a road; **no vehicle ever on a collision course** | *Every* urgency-3 is a false positive. Target after the gate: 0 |
| **B** — parked-car block | One block lined with parked cars, normal pace | Every announcement is counted; no vehicle is a threat |
| **C** — staged head-on | Teammate walks directly at the wearer from ~15 m, ×3 | Each repeat **must** produce urgency-3. This is the guard against a gate that suppresses everything |

Video is still worth capturing for the demo reel and as a sanity check, but no
number in the report depends on annotating it.

**This is a deviation from the PRD's stated measurement method** — see Open
Deviations below.

## Patterns to Mirror

| Category | Source | Pattern |
|---|---|---|
| Decorator over a sink | `backend/src/cane/bridge/uplink.py:95` | `MonitoredUplink` wraps an uplink and forwards; the recorder wraps `EventSink` the same way |
| Protocol seam | `backend/src/cane/bridge/pipeline.py:109` | `EventSink` is a `Protocol` with `async def send_event(...) -> bool`; satisfy it structurally, do not subclass |
| Wiring a decorator | `backend/src/cane/bridge/__main__.py:206` | `build_uplink()` composes the chain in one place and returns it; add composition there, not in the pipeline |
| Measurement harness | `backend/src/cane/benchmarks/yolo.py:27` | `parser()` + `--output` directory that **rejects existing directories**; writes `.jsonl` + `.csv` side by side |
| Reject, never clamp | `backend/src/cane/cloud/motion.py:8` | A bad sample is dropped with a counter, not coerced into a plausible value |
| Python logging | `backend/src/cane/bridge/snapshot.py:25` | `logging.getLogger("cane.bridge.<module>")` |
| Python tests | `backend/tests/bridge/conftest.py` | `pytest` + module-scoped fixtures under `backend/tests/<area>/`, docstring explains the fixture's cost |
| Scripted walk as a test | `ios/CaneKit/Tests/CaneFeedbackTests/WalkTimelineTests.swift:10` | Drive `FeedbackManager` with a `TestClock` and a `RecordingSink`, assert the exact announcement sequence |
| Driving the manager | `ios/CaneKit/Sources/CaneFeedback/FeedbackManager.swift:29,55` | `public init(sink:ambientSink:clock:)`; `ingest(_ data: Data, now:)` takes raw JSON, so the CLI needs no decoder of its own |
| Counting sink | `ios/CaneKit/Sources/CaneFeedback/AnnouncementSink.swift:5` | `AnnouncementSink` / `AmbientSink` are public protocols — implement, don't fork |
| Dev-tool executable | `ios/CaneKit/Package.swift` (`NavCLI`) | `.executableTarget` for a laptop-only tool, kept out of the app's dependency graph |

**Deliberate divergence from the `NavCLI` precedent:** `NavCLI` has no test
target. The replay tool produces the numbers this milestone exists to report, so
the tally logic goes in a **testable library target** (`CaneReplay`) with a thin
executable on top. An untested measuring instrument is not evidence.

## Files to Change

| File | Action | Why |
|---|---|---|
| `backend/src/cane/bridge/recorder.py` | CREATE | `RecordingEventSink`: satisfies `EventSink`, appends one JSON object per event, forwards to the inner sink unchanged |
| `backend/src/cane/bridge/__main__.py` | UPDATE | Add `--record-events PATH`; compose the recorder around the event sink beside `build_uplink` |
| `backend/tests/bridge/test_recorder.py` | CREATE | Forwarding is transparent; the return value is the inner sink's; a write failure never breaks the uplink |
| `ios/CaneKit/Sources/CaneReplay/ReplayTally.swift` | CREATE | Pure counting: announcements by urgency, by class, speech vs haptic-only, per minute |
| `ios/CaneKit/Sources/CaneReplay/ReplayRunner.swift` | CREATE | Reads the JSONL, drives `FeedbackManager` on a virtual clock, returns a `ReplayTally` |
| `ios/CaneKit/Sources/ReplayCLI/main.swift` | CREATE | Thin argument parsing + report printing over `CaneReplay` |
| `ios/CaneKit/Tests/CaneReplayTests/ReplayTallyTests.swift` | CREATE | Tally arithmetic, including the per-minute rate over a known span |
| `ios/CaneKit/Tests/CaneReplayTests/ReplayRunnerTests.swift` | CREATE | Determinism (same input → same tally, twice) and a hand-built trace with a known expected tally |
| `ios/CaneKit/Package.swift` | UPDATE | Add the `CaneReplay` library, `ReplayCLI` executable and `CaneReplayTests` targets |
| `docs/steps/step-28-brief.md` | CREATE | The walk protocol as a runnable checklist, mirroring the existing step-brief format |
| `docs/baseline-walk.md` | CREATE | The measured result: counts, conditions, date, route description |
| `startcane` | UPDATE | Document `--record-events` in the header comment ("when a run command changes anywhere in the repo, change it here too") |

## Tasks

### Task 1: Record the event stream

- **Action**: Add `RecordingEventSink(inner: EventSink, path: Path)` in
  `backend/src/cane/bridge/recorder.py`. `send_event` appends one JSON line —
  the event's wire form plus a laptop-monotonic `t_emit_ms` — then delegates and
  returns the inner sink's result. Open the file once, flush per line so a
  killed walk keeps its data. **A write failure logs and continues**: losing the
  recording must never drop a warning to the wearer.
- **Mirror**: `MonitoredUplink` (`uplink.py:95`) for the decorator shape;
  `EventSink` (`pipeline.py:109`) for the protocol.
- **Validate**: `cd backend && uv run pytest tests/bridge/test_recorder.py -q`

### Task 2: Wire the flag

- **Action**: Add `--record-events PATH` to the bridge CLI. Absent → the chain is
  byte-identical to today. Present → wrap the event sink. Refuse to overwrite an
  existing file.
- **Mirror**: `build_uplink()` (`__main__.py:206`) for where composition lives;
  `benchmarks/yolo.py:44` for rejecting an existing output path.
- **Validate**: `cd backend && PYTHONPATH=src uv run python -m cane.bridge --source synthetic --duration-s 5 --record-events /tmp/t.jsonl && wc -l /tmp/t.jsonl`

### Task 3: Replay and tally

- **Action**: `CaneReplay` reads the JSONL and feeds each line to
  `FeedbackManager.ingest(_ data:now:)`, driving `now` from the recorded
  `t_emit_ms` so the run is deterministic and independent of wall-clock. A
  counting `AnnouncementSink` accumulates: total announcements, count by urgency
  0–3, count by class, speech vs haptic-only, and each rate per minute over the
  recording's span.
- **Mirror**: `WalkTimelineTests.swift:10` for the drive loop and the
  `TestClock`/`RecordingSink` shape; `AnnouncementSink.swift:5` for the protocol.
- **Validate**: `cd ios/CaneKit && swift test --filter CaneReplayTests`

### Task 4: The CLI

- **Action**: `replay-cli <recording.jsonl>` prints the tally as a table and
  writes a `.csv` beside the input. No logic beyond argument handling and
  formatting.
- **Mirror**: `NavCLI` for the target shape; `benchmarks/yolo.py` for writing
  results next to the input.
- **Validate**: `cd ios/CaneKit && swift run replay-cli /tmp/t.jsonl`

### Task 5: Write the walk protocol

- **Action**: `docs/steps/step-28-brief.md` — the three takes (A, B, C) with
  their route properties, the exact bridge command for each, rig setup (X4 on the
  shoulder, laptop in the backpack, `--allow-unsynced` because there is no phone
  in the loop, X4 screen timer set to never or it drops off USB), and the
  safety rule: **the wearer is sighted and accompanied; nobody walks blindfolded
  beside live traffic.**
- **Mirror**: the existing `docs/steps/step-NN-brief.md` format.
- **Validate**: a teammate can execute it without asking a question.

### Task 6: Walk it and report

- **Action**: Run takes A, B and C. Replay each. Write `docs/baseline-walk.md`
  with the counts, the date, the route, the weather and the traffic density, so
  the milestone-4 re-run can be judged comparable.
- **Validate**: `docs/baseline-walk.md` states a number for every row of the
  PRD's metric table, or says explicitly why a row could not be measured.

## Validation

```bash
# Both suites, from the repo root
./startcane test

# Or individually
cd backend && uv run pytest -q
cd ios/CaneKit && swift test

# The recorder end to end, no hardware
cd backend && PYTHONPATH=src uv run python -m cane.bridge \
  --source synthetic --duration-s 10 --record-events /tmp/baseline-smoke.jsonl

# Determinism: the same recording must tally identically twice
cd ios/CaneKit
swift run replay-cli /tmp/baseline-smoke.jsonl > /tmp/a.txt
swift run replay-cli /tmp/baseline-smoke.jsonl > /tmp/b.txt
diff /tmp/a.txt /tmp/b.txt && echo "deterministic"

# No behaviour change without the flag: the existing live path stays green
cd backend && CANE_LIVE_X4=1 uv run pytest -q -k live
```

Baselines to beat: `backend` 1096 passed / 2 skipped, `ios/CaneKit` 433 passed / 0 failures.

## Risks

| Risk | Likelihood | Mitigation |
|---|---|---|
| Recording I/O stalls the bridge loop and costs frames | Low | One append of a few hundred bytes per event at 3 fps; measure with `--source synthetic` before the walk and drop to buffered writes if the tick budget moves |
| Replay diverges from what the phone would really do | Medium | The divergence is the wall-clock: replay drives `now` from `t_emit_ms`, the phone from its own clock. Run take C once with the real phone and confirm the Stop count matches. This is the only take that needs `Local.xcconfig` |
| Traffic on the day is too sparse to produce a meaningful count | Medium | Pick the route for traffic density, not convenience; require ≥ 20 passing vehicles in take A or the take does not count |
| The walk is unsafe | Low | **High impact.** Sighted wearer, accompanied, daylight, sidewalk with a kerb separation. No blindfold anywhere near a road |
| X4 drops off USB mid-walk (known: screen sleep) | Medium | Set the X4's screen timer to never before leaving; `wc -l` the recording immediately after each take, before walking back |
| Tally logic is wrong and the milestone reports fiction | Low | **High impact.** This is why `CaneReplay` is a tested library rather than a script — a hand-built trace with a known expected tally is the guard |
| `Local.xcconfig` blocks the phone confirmation | Medium | Only take C needs it, and only as a cross-check. Copy `Local.xcconfig.example` and fill in a team ID; if no Apple Developer account is available, record the limitation in `docs/baseline-walk.md` rather than skipping the milestone |

## Open Deviations from the PRD

1. **Measurement method.** The PRD says false Stops are reconciled against
   hand-annotated video. This plan makes ground truth structural instead (take A
   has no true threats by construction), and treats video as optional. If you
   want annotation kept as a hard requirement, say so and Task 6 grows.
2. **Metric coverage.** Milestone 1 can produce every PRD metric *except*
   ground-hazard lead time and false-positive rate, which have nothing to
   measure until milestone 3 exists. `docs/baseline-walk.md` will mark those two
   rows "not yet applicable" rather than leaving them blank.

## Acceptance

- [ ] All six tasks complete
- [ ] `./startcane test` green, at or above the 1096 / 433 baselines
- [ ] Bridge behaviour byte-identical without `--record-events`
- [ ] The same recording tallies identically on two consecutive runs
- [ ] Takes A, B and C recorded, with ≥ 20 passing vehicles in take A
- [ ] Take C produced a Stop on all three repeats (the instrument can still see a real threat)
- [ ] `docs/baseline-walk.md` states a number, or an explicit "not applicable", for every PRD metric row
- [ ] Patterns mirrored, not reinvented
