# TDD Evidence — Threat-Aware Hazard Detection, milestone 1

**Source plan**: `.claude/plans/threat-aware-hazards.plan.md`
**Source PRD**: `.claude/prds/threat-aware-hazards.prd.md`
**Milestone**: 1 — Trust baseline measured
**Date**: 2026-09-26
**Scope implemented**: plan tasks 1–5. Task 6 is fieldwork and is left to the team.

## User journeys

Taken from the plan; none invented here.

1. As the team, we want the live event stream recorded to disk during a walk, so a baseline can be
   measured without the phone app in the loop.
2. As the team, we want the recording replayed through the real gauntlet, so we learn what the phone
   *would* have announced.
3. As the team, we want the same recording to tally identically every time, so the step-29
   before/after is a comparison of the gate and not of the traffic.
4. As the wearer, I want the recorder to be incapable of degrading the device, so measuring safety
   never costs safety.

## Task report

### Task 1 — Record the event stream

Added `RecordingEventSink` (a decorator over the `EventSink` protocol) and `open_recording`.

- **RED**: `uv run pytest tests/bridge/test_recorder.py -q`
  → `ModuleNotFoundError: No module named 'cane.bridge.recorder'`, 1 error during collection.
- **GREEN**: same command → `11 passed in 0.04s`.
- **Guaranteed**: the inner sink's result is returned unchanged; each event is one JSON line of the
  wire form plus `t_emit_ms`; every line is flushed; a write fault is counted and swallowed;
  `open_recording` refuses to overwrite.

### Task 2 — Wire the flag

Added `--record-events PATH`, `BridgeConfig.record_events`, and `wrap_event_sink`.

- **RED**: `uv run pytest tests/bridge/test_recorder.py tests/bridge/test_cli.py -q`
  → `ImportError: cannot import name 'wrap_event_sink'`.
- **GREEN**: same command → `34 passed in 1.08s`.
- **Guaranteed**: without the flag, `wrap_event_sink` returns *the very same object*, so "no flag, no
  change" is checkable by identity rather than by reading the decorator and believing it.

### Tasks 3 & 4 — Replay, tally, CLI

Added the `CaneReplay` library (`ReplayTally`, `ReplayRunner`) and the `replay-cli` executable.

- **RED**: `swift build --target CaneReplayTests`
  → `error: target 'ReplayCLI' referenced in product 'replay-cli' is empty` (compile-time RED).
- **Intermediate RED, and a real finding**: first full run gave 2 failures —
  `XCTAssertEqual failed: ("1") is not equal to ("3")`. Cause was **the test fixture, not the
  implementation**: it held `t_capture_ms` constant while advancing the emit stamp, so the staleness
  gate correctly dropped every event after the first TTL. Fixed by advancing capture and emit
  together (`Fixture.lineAt`), which is what a real recording does.
- **GREEN**: `swift test --filter CaneReplayTests` → `Executed 17 tests, with 0 failures`.
- **Guaranteed**: see the specification table below.

### Task 5 — Walk protocol

`docs/steps/step-28-brief.md` plus a pointer in `startcane`'s header. Not unit-testable; verified by
`bash -n startcane` and by the end-to-end run below.

### End-to-end (not a unit test)

Bridge against the dev stub server, no phone connected, no cloud:

```
events 0 undelivered 24 ... local=fake | mode cloud(1) rung 1
=== recorded lines ===  24
```

**24 events undelivered because no phone was connected, and 24 lines recorded.** This is the
premise of the whole milestone — the baseline does not need the phone — demonstrated rather than
asserted.

Replaying that same recording through `replay-cli`:

```
walk.jsonl
  events replayed           24   (0 rejected)
  span                  0m 07s
  announcements              6     47.7 /min
  urgency 3  Stop            0      0.0 /min
  urgency 1  aware           6     47.7 /min
  by class    person         6
```

Consistent with the stub's output: `person`, `mid` band, no closing rate → urgency 1 on the moving
ladder, and the 8 s urgency-1 refractory collapsing 24 events onto 6 tracks.

## Test specification

| # | What is guaranteed | Test | Type | Result |
|---|---|---|---|---|
| 1 | The event reaches the inner sink unchanged | `test_recorder.py::test_forwards_the_event_unchanged` | unit | PASS |
| 2 | The inner sink's result is returned verbatim (True and False) | `test_recorder.py::test_returns_the_inner_sinks_result` | unit | PASS |
| 3 | **A disk fault never stops a warning reaching the wearer** | `test_recorder.py::test_a_write_failure_never_stops_the_warning` | unit | PASS |
| 4 | One JSON line per event, in order | `test_recorder.py::test_writes_one_json_line_per_event` | unit | PASS |
| 5 | The line is the wire form plus `t_emit_ms` | `test_recorder.py::test_the_line_is_the_wire_form_plus_the_emit_stamp` | unit | PASS |
| 6 | A recorded line parses back into the identical event | `test_recorder.py::test_the_recording_parses_back_into_the_same_event` | contract | PASS |
| 7 | Every line is flushed, so a killed walk keeps its data | `test_recorder.py::test_flushes_every_event_so_a_killed_walk_keeps_its_data` | unit | PASS |
| 8 | An existing recording is never overwritten | `test_recorder.py::TestOpenRecording` (3 cases) | unit | PASS |
| 9 | **No flag → the identical sink object** | `test_recorder.py::test_no_recording_returns_the_very_same_sink` | unit | PASS |
| 10 | The flag is threaded into the frozen config; default is off | `test_cli.py` (2 cases) | integration | PASS |
| 11 | The tally never mutates in place | `ReplayTallyTests::testAddingDoesNotMutateTheOriginal` | unit | PASS |
| 12 | Counts split by urgency, class, and speech vs haptic-only | `ReplayTallyTests` (4 cases) | unit | PASS |
| 13 | Rates use the observed span; a zero span yields 0, not infinity | `ReplayTallyTests` (3 cases) | unit | PASS |
| 14 | A fast near-band car produces exactly one Stop | `ReplayRunnerTests::testAFastCarInTheNearBandProducesAStop` | integration | PASS |
| 15 | A receding car is never announced | `ReplayRunnerTests::testARecedingCarIsNeverAnnounced` | integration | PASS |
| 16 | **The same recording tallies identically twice** | `ReplayRunnerTests::testTheSameRecordingTalliesIdenticallyTwice` | integration | PASS |
| 17 | A recorded line carrying `t_emit_ms` replays cleanly (the contract rejects unknown keys) | `ReplayRunnerTests::testARecordedLineCarryingItsEmitStampReplaysCleanly` | integration | PASS |
| 18 | A truncated recording still yields its numbers | `ReplayRunnerTests::testAMalformedLineIsCountedAndTheRestStillReplays` | integration | PASS |
| 19 | A line with no emit stamp is rejected, not silently mis-timed | `ReplayRunnerTests::testALineWithoutAnEmitStampIsRejected` | integration | PASS |
| 20 | Blank lines are ignored, not counted as faults | `ReplayRunnerTests::testBlankLinesAreIgnoredRatherThanRejected` | integration | PASS |

## Coverage and known gaps

Full suites, final run:

```
backend        1365 passed, 1 skipped in 64.75s
ios/CaneKit     526 passed, 0 failures in 12.7s
```

Both above their documented baselines (1096 / 433). 37 tests added.

**One transient**: an earlier full backend run failed
`tests/cloud/test_app.py::test_non_inference_overhead_p50_under_budget` with
`p50 20.18 ms (load 5.5 on 8 cores)`. It passed in isolation and passed in the final full run. This
is the load-sensitive latency test the runbook already documents; it is unrelated to these changes,
which touch only `cane.bridge` and a new Swift target.

**Known gaps, deliberate:**

- `ReplayCLI/main.swift` has no tests — it is argument parsing and string formatting over the tested
  library, matching the untested `NavCLI` precedent. The logic that produces numbers is in
  `CaneReplay`, which is tested.
- No E2E in the Playwright sense; there is no UI. The end-to-end above (bridge → recording →
  replay) is the equivalent, and it was run by hand rather than automated because it needs a live
  bridge process.
- **The replay models decisions, not speaker time.** The sink returns instantly, so totals are an
  upper bound on what a wearer would hear. `urgency 3` is exact. Documented in `ReplayTally`'s
  doc comment and printed by the CLI on every run.
- Plan task 6 (the actual walk) is not done: it is fieldwork.

## Merge evidence

No checkpoint commits were made — the working tree is left staged for review, since the user has not
asked for a commit. If these changes are squashed, the RED/GREEN evidence above is the record.
