# Step 28 — The trust baseline (the walk)

Binding spec: `.claude/prds/threat-aware-hazards.prd.md` milestone 1, and
`.claude/plans/threat-aware-hazards.plan.md`.
This is a **physical** step: a person wears the rig and walks outside. No agent can do it.

Its output is a number, not a feature. Steps 29+ (the path-conflict gate, the ground channel) are
judged against it, so if this is done sloppily every later claim is unfalsifiable.

## 0. Why the walk is recorded and replayed, not measured live

A walk cannot be re-run. Traffic density, vehicle speed, your pace and the route all differ between
takes, so comparing a "before" walk against an "after" walk measures the traffic.

So: **record the event stream once, replay it as often as you like.** The recording is the
experiment; the replay is the measurement. When step 29's gate lands, the same recording replays
through the changed gauntlet and the difference is the gate, with nothing else moving.

## 1. What this walk needs — and what it does not

| | needed? | why |
|---|---|---|
| Insta360 X4 | **yes** | it is the 360° half; the whole question is about traffic you cannot see |
| Laptop + backpack | **yes** | capture, detection and recording all run here |
| iPhone / the app | **no** | the feedback manager is a macOS-buildable library. `replay-cli` runs the *same* gauntlet on the laptop afterwards |
| The L4 / any cloud | **no** | see §1.1 |
| A blindfold | **never** | see §4 |

### 1.1 The local detector is enough — unlike step 25

Step 25 needed the cloud path because steering needs depth, and the local backend is built with
`depth=None` on purpose. **This step does not need depth.** `range_band` and `closing_mps` come from
box-height geometry in `cane/cloud/motion.py`, and the local backend runs *the same*
`InferenceService` in-process (`bridge/backends.py:274`) — only its depth head is off.

So the walk costs no cloud money, needs no ephemeral-IP firewall check, and cannot be spoiled by the
venue's uplink. Run it local.

## 2. Before you leave the building

- [ ] **X4 screen timer → Never.** It drops off USB when the screen sleeps, mid-walk, silently.
- [ ] `caffeinate -i` is in the command below. Lid closed in a backpack also wants
      `sudo pmset -a disablesleep 1` once (undo with `0`).
- [ ] Disk space: a 5-minute take is a few MB, but check anyway.
- [ ] **Dry-run indoors first** with `--source synthetic --duration-s 10` and confirm the recording
      has lines in it. Discovering the flag is misspelled while standing next to a road is a wasted trip.
- [ ] `mkdir -p backend/logs/walks`
- [ ] Phone with a video camera, if you want demo footage. Nothing measured depends on it (§5).

## 3. The three takes

Ground truth is structural: the route is chosen so the right answer is known without annotating
anything.

| take | route | duration | what is true by construction |
|---|---|---|---|
| **A** traffic | sidewalk beside a road carrying real traffic, **kerb-separated**, where no vehicle is ever on a collision course with you | 5 min, **≥ 20 passing vehicles** | *every* urgency-3 is a false positive |
| **B** parked | one block lined with parked cars, normal walking pace | ~90 s | no vehicle is a threat; this is the announcement-rate baseline |
| **C** head-on | a teammate walks **directly at you** from ~15 m, ×3, on an empty path | ~60 s | each repeat **must** produce an urgency 3 |

Take C is not optional. Without it, step 29 could score a perfect zero on take A by suppressing
everything, and you would not find out until the demo.

Run each from `backend/`, changing only the output name:

```bash
PYTHONPATH=src caffeinate -i uv run --env-file .env \
  --extra yolo --extra vision --extra mac \
  python -m cane.bridge \
    --source x4 --rung 3 --delta-deg 0 \
    --start-mode local --local-detector ultralytics \
    --allow-unsynced \
    --duration-s 300 \
    --record-events logs/walks/take-a-traffic.jsonl
```

- `--allow-unsynced` is what lets the walk run with **no phone**: the laptop clock stamps the frames.
- `--start-mode local` keeps it off the cloud. Nothing is listening on `127.0.0.1:8000`, so the
  health prober fails, stays local, and that is correct.
- `--record-events` **refuses to overwrite**. That is deliberate — a take cannot be re-run under the
  same traffic. Pick a new name rather than deleting one.
- Take B: `--duration-s 90`, `take-b-parked.jsonl`. Take C: `--duration-s 60`, `take-c-headon.jsonl`.

**Check each take before you walk back:**

```bash
wc -l backend/logs/walks/take-a-traffic.jsonl
```

Zero lines means the X4 dropped off USB or the detector never loaded. Fix it while you are still
outside.

## 4. Safety — read this before §3

- The wearer is **sighted** and walks with someone. Nobody is blindfolded anywhere near a road.
- Daylight. Kerb-separated sidewalk. Not a shared-use path, not a parking lot entrance.
- The rig is a recorder on this walk: it announces nothing to anyone, so it cannot mislead the
  wearer. Do not "test the warnings" in traffic.

## 5. After the walk: replay

```bash
cd ios/CaneKit
swift run replay-cli ../../backend/logs/walks/take-a-traffic.jsonl
```

It prints the tally and writes `take-a-traffic.tally.csv` beside the recording. Run it twice on the
same file and confirm you get the same numbers — the replay is deterministic by construction
(the virtual clock is driven from each line's `t_emit_ms`), and if it is not, stop and say so.

**Read the caveat the tool prints.** The tally counts what the gauntlet *decided*, not seconds of
audio: the replay sink returns instantly, so totals are an upper bound on what a wearer would
actually hear. `urgency 3` is exact, because a Stop preempts everything and always speaks — and
urgency 3 is the number this milestone is about.

## 6. What to write down

Fill in `docs/baseline-walk.md`:

- date, time of day, weather, and a one-line description of each route (enough that the step-29
  re-run can be judged comparable)
- vehicles counted by hand during take A
- the full tally for each take
- for every metric row in the PRD's table: the number, or an explicit "not applicable yet" —
  ground-hazard lead time and false-positive rate have nothing to measure until milestone 3
- anything that went wrong, including takes you threw away

## 7. Gotchas already paid for

- **`pkill -f` matches your own command line.** Kill the bridge with Ctrl-C or a script file.
- **The X4 sleeps.** Said twice on purpose; it is the most common way a take comes back empty.
- **`--local-detector ultralytics` needs the venv that has ultralytics.** If it exits with
  `No module named ultralytics`, that hard exit is step 8's no-silent-fake guard working correctly,
  not a bug. Run it as `UV_PROJECT_ENVIRONMENT=.venv-yolo uv run --extra yolo --extra mac ...`.
- **The recorder never perturbs the run.** It forwards the uplink's result unchanged and swallows
  its own write faults after logging, so a full disk degrades the recording and never the device.
