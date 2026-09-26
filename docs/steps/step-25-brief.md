# Step 25 — Live X4 steering validation (the walk)

Binding spec: `docs/shepherd-steering-plan.md` step 25, plus every ruling in §3.1–§3.4.
This is a **physical** step: a person wears the rig. No agent can do it.

## 0. The trap: the walk cannot be done in local-fallback mode

Step 23 sets `depth=None` on the local backend on purpose (a `GapProfile` nobody sends is not worth
92 ms/tick, and plan §4.2 forbids emitting a steer frame with no depth behind it). **So a bridge
running in local-fallback mode produces no `steer` frames at all, and there is nothing to validate.**

Steering therefore needs the *cloud* code path. Two ways to have it:

| option | what it is | cost | when to pick it |
|---|---|---|---|
| **A — service on the laptop** | run `cane.cloud.app` on `localhost:8000` and point the bridge at it. This is the cloud *path*, not local fallback, so depth is ON | YOLO ~178 ms + depth ~92 ms sharing **one** Apple MPS GPU ≈ 270 ms/frame against a 333 ms tick at 3 fps | the default for the walk; no money, no network |
| **B — the real L4** | `l4-infer` in `us-east1-d`, TensorRT | ~$0.70/h while up, and the external IP is ephemeral so the firewall /32 needs re-checking | only if A cannot hold 3 fps, or to get the step-22 L4 number the plan still calls UNVERIFIED |

**Option A caveat, known in advance:** both models on one GPU is the exact configuration that made the
step-8 fallback drill oscillate. We are not drilling the fallback here, but if the health monitor
starts stepping rungs on its own, that is why — read the rung from the stats line before blaming the
steering. If p95 RTT sits above 500 ms the monitor will step down, and that is correct behaviour, not
a bug to chase mid-walk.

**Verify before walking** (the exact invocation was not smoke-tested while step 27 was still editing
`cane.cloud`): start the service, `GET /warmup`, and confirm the response reports a warmed **depth**
model as well as the detector. If `/warmup` reports no depth, the walk will silently produce zero
steer frames — check this first, every time.

## 1. Preconditions

- [ ] X4 on the shoulder, **screen sleep set to never** (it drops off USB when the screen sleeps).
- [ ] Phone on the chest, **screen facing outward** — the front/selfie camera is the one that looks
      where the wearer looks; the rear cameras see the wearer's chest.
- [ ] `delta_deg` measured for the actual mount and set. **Above 15° a third of the forward arc goes
      unseen** (step 27 adds a startup warning); above 30° the bridge refuses to start, by design.
      Straighten the camera rather than raising the bound.
- [ ] Bridge started with `--scene` if the voice half is being demonstrated (that is what publishes the
      ring to the Gemini path, step 26).
- [ ] `/warmup` returns a warmed depth model (see §0).
- [ ] Two people: one wears the rig, one reads the stats line and takes notes. The wearer must not be
      the one reading a laptop.

## 2. The three set pieces

Run each three times. Record per run: the ring, the chosen gap, `steer`, `proximity`,
`proximity_anchored`, `blocked`, and the latency from `t_capture_ms` to cue onset.

1. **Corridor with one side blocked.** Walk a corridor with a wall, row of chairs or hedge close on one
   side and open on the other. *Expect:* `steer` consistently signed toward the open side; the wearer
   drifts that way without being told in words.
2. **Doorway.** Approach a door or gap roughly body-width plus a little. *Expect:* the gap is found as
   **one** run even when it straddles a crop seam, and `blocked` stays false while the opening is wide
   enough at its own distance.
3. **Person standing in the path.** A person stands still directly ahead at ~2 m. *Expect:* the
   detection path announces them (existing behaviour) **and** the ring routes around them. This is the
   one set piece where both halves of the system speak about the same object — check they do not
   contradict.

## 3. Pass criteria

- All three set pieces pass with **the wearer taking the passable side unaided** — no coaching, no
  laptop-watching.
- Measured p50/p95 from `t_capture_ms` to cue onset, written into this brief and compared to
  Shepherd's published <150 ms. Say where we actually sit; do not round in our favour.
- The step-22 depth cost re-measured in the real posture used for the walk.
- **At least one failure mode found and written down.** If the walk finds nothing, the walk was too
  easy — glass, a downward step (a curb, a kerb edge, stairs going down) and a low overhang are the
  likely ones, and none of them are in the 12-class detector enum either.

## 4. What to watch for, from what the build already knows

- **Step 27 landed, so expect `proximity_anchored` to be TRUE — and treat false as a problem to fix,
  not a mode to demo.** The affine fit anchors 3 of 3 forward crops on a real X4 frame (rms
  0.068/0.005/0.013). Two reasons this matters on the walk:
  - *Anchored is the good case:* verify a closing wall actually raises `abs(steer)` in metres. A
    scale-free rule cannot do that, so it is the single cheapest proof the metric path works outdoors.
  - *Unanchored is now known-unsound on the side, not merely vague* (§3.5 ruling 1, step 29 open):
    across three crops the scale-free rule compares incommensurable numbers, and under the measured
    pairs a **uniform 6 m scene reads 3.12 / 3.65 / 4.04**, so it can report "free left, blocked right"
    in an empty corridor. If the venue rejects the anchor, **do not demo the ring's side** — fall back
    to the detection path, or land step 29 first.

- **Three ways the anchor can be confidently wrong, all measured, none caught by the residual guard**
  (§3.5 ruling 4). These are the walk's real preparation:
  - **Measure `MOUNT_HEIGHT_M` properly.** A 20% error gives a 20% distance error at residual 0.000 —
    the fit reports a perfect score while every distance is wrong. Tape-measure the camera's height on
    the actual wearer, do not estimate.
  - **Keep the camera level.** A 20% up-slope gives a 17% scale error, again at residual 0.000.
  - **A crowded floor is a failure mode, not a nuisance.** A floor whose near half is hidden by people
    inflated `a` **4×** at a residual *inside* the ceiling — reporting everything four times farther
    than it is. Walk the set pieces with a clear floor first, then deliberately try one with people
    standing near, and write down what happens. This is the most likely way the demo lies.
- **A structureless ring reports `blocked`** (§3.3 ruling 3). A blank wall filling the view, or a
  featureless outdoor scene, may over-block. Note where it happens; over-blocking is the safe way to
  be wrong but it is still worth knowing which surfaces do it.
- **The cue is silent below proximity 0.35** (≈2.16 m, step 24). Nothing firing in an open space is
  correct, not broken.
- **Is the 20 ms / 0.20-sharpness tick felt through clothing at all?** Untested. If not, the fix is a
  gain tier, not a redesign.
- **Does 200–700 ms read as a gradient, or as two states?** Untested, and it decides whether the
  pulse-rate mapping is worth keeping.
- **`CaneApp` has never been compiled** — it is an xcodegen target that was only parse-checked. The
  first device build is the first real test of the step-24 wiring. Budget time for that before walking.

## 5. Results

*(fill in during the walk — runs, numbers, and the failure modes found)*
