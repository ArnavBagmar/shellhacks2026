# Step 23 brief — Obstacle detection, gap profiling and the steering law in the bridge

Implements `docs/shepherd-steering-plan.md` step 23 (§2.2, §2.3, §3, §3.1). Depends on step 21
(the `steer` frame) and step 22 (`GapProfile` on `InferenceResponse`). Nothing in the detection
path changed: every pre-existing `tests/bridge` and `tests/transport` test passes untouched.

## A. What was built

| file | what it owns |
|---|---|
| `backend/src/cane/bridge/gap_ring.py` | 3 × 16 profiles → one 48-column ring in torso bearings; `SMOOTH_KERNEL` across the seams |
| `backend/src/cane/bridge/gap_anchor.py` | raw relative depth → metres, and the two §2.3 guards that decide whether that is allowed |
| `backend/src/cane/bridge/steering.py` | passability, run finding, the gap, `proximityFactor`, the steering law |
| `backend/src/cane/bridge/steer_frame.py` | one `InferenceResponse` → one `Steer`, or honestly nothing; the ±90 invariant |
| `backend/src/cane/bridge/pipeline.py` | `steer_sink` (a second, separate `Protocol`), one frame per answered tick, `steer_sent`/`steer_undelivered` |
| `backend/src/cane/bridge/backends.py` | `LOCAL_DEPTH_ENABLED = False`: the local fallback builds no depth head |
| `backend/src/cane/bridge/__main__.py` | passes the `EventLinkServer` as both sinks |
| `backend/src/cane/transport/server.py` | `send_steer`, on the socket the events already hold open |

The first three modules are pure — no clock, no socket, no pipeline — which is what let the
geometry be tested at all. 103 new tests across six files; `tests/bridge` + `tests/transport`
= **532 passed, 1 skipped**.

Constants, all named, all in one module each: `DEPTH_CROP_INDICES (5, 0, 1)`, `RING_COLUMNS 48`,
`COLUMN_WIDTH_DEG 3.75` (derived from `CROP_FOV_DEG / COLUMNS_PER_CROP`), `SMOOTH_KERNEL`,
`D_STOP_M 0.60`, `D_CLEAR_M 3.00`, `BODY_WIDTH_M 0.70`, `GAP_DIRECTION_SCALE_DEG 90`,
`WIDTH_TIE_COLUMNS 1`, `UNANCHORED_PROXIMITY 0.5`, `UNANCHORED_PASSABLE_FREEDOM 0.5`,
`UNANCHORED_MIN_GAP_DEG` (derived: `required_width_deg(D_CLEAR_M)`), `RELATIVE_DEPTH_SCALE_M 1.8`,
`GROUND_FIT_RESIDUAL_MAX 1.0`, `DISTANCE_MAX_M 10.0`, `CAMERA_BEARING_ABS_MAX_DEG 90` (the invariant is asserted in camera-frame bearings, before `delta_deg`), `MAX_EMITTED_BEARING_ABS_DEG 120` (derived, documented, not enforced) and `DELTA_DEG_MAX 30.0`.

## B. Decisions the plan left open

1. **The width test is evaluated at the run's *nearest* column.** The angle a body subtends
   shrinks with distance, so the nearest column is the conservative reading: the gap must be wide
   enough where it is tightest.
2. **The "widest run" tie-break.** Runs within `WIDTH_TIE_COLUMNS` of the widest are treated as
   equally wide and the smallest |bearing| wins. Wider by two columns still wins outright — the
   tolerance is a tolerance, not a rule that always prefers straight ahead.
3. **`d_min` is taken over the whole arc, not the passable part.** The wall you are about to walk
   into is by definition impassable; excluding it would make an approach invisible, and
   acceptance 1 (|steer| rising as a wall closes) is only satisfiable this way.
4. **The anchor is per *frame*, not per crop.** `d_min` spans crops, so one number cannot be half
   trusted. §2.3's own wording ("the frame's anchor") is this reading.
5. **When blocked, `steer` still carries the least-bad direction** (ruling 2's rationale), at the
   magnitude the proximity term gives it. `blocked` is what says "not a path".
6. **The ±90 invariant is asserted on the ring, not on the emitted bearing.** See spec bug 3.

## C. The live path is the *unanchored* one

Step 22 measured the §2.3 one-parameter ground fit rejected on all three forward crops of a real
X4 frame (residual ratios 0.686 / 0.922 / 0.892 against its 0.25 ceiling), because Depth Anything
V2 is affine-invariant in disparity (`v = a/d + b`, both unknown per frame) and the offset
dominates. So `anchored` is `false` in the field and there are **two passability rules**:

* **anchored** (no live evidence; kept alive by tests): metres, `D_STOP_M`, a body's width at the
  run's own distance, Shepherd's ramp for `proximity`.
* **unanchored** (what runs): `relative_freedom` = `(v_nearest − v) / (v_nearest − v_farthest)`.
  Being a ratio of differences, **both `a` and `b` cancel exactly** — so it is a ranking in the
  §1.1 sense, and it is *more* robust than a fixed metric threshold, which an unknown offset makes
  meaningless. `freedom ≥ 0.5` is, in metres, the harmonic mean of the nearest and farthest
  distance in view — about twice the nearest obstacle's distance when the view is open.
  `proximity` is the constant `UNANCHORED_PROXIMITY = 0.5` (the plan's own Risks-1 contingency,
  verbatim) with `proximity_anchored = false` beside it. It is **not** a ramp computed from the
  placeholder scale: a guessed metre laundered through a band would be exactly the silent fake
  step 8 forbids.

Two honest consequences, both pinned by tests:

* **A closing wall is invisible without an anchor.** A scale-free rule is scale-invariant, so a
  wall at 0.30 m in a 3 m view and one at 0.60 m in a 6 m view produce byte-identical frames.
  Acceptance 1's monotonic |steer| rise is achievable **only on the anchored path**.
  `proximity_anchored = false` is precisely the warning that urgency is unavailable.
* **A structureless ring is reported `blocked`.** With no scale, "uniformly far" and "uniformly
  near" are the same picture, so a flat ring yields no gap. Over-blocking is the safe way to be
  wrong; claiming a clear path would not be. Real frames have ample contrast (§4.5 measured
  1.935…3.011 on one crop), so this is mostly a synthetic case.

**The seam for the follow-up affine fit** is `DepthAnchor` in `gap_anchor.py`: it is the only
thing that converts depth to metres, and `solve_steering` switches on `anchor.accepted` alone.
A fitted scale needs to arrive as **two** numbers per frame (scale *and* offset, `d = a / (v − b)`)
on `GapProfile`, at which point `DepthAnchor` gains the offset, `distance_for_depth` gains one
term, and the metric path lights up with no other change.

## D. Worked examples (real output of this code)

A left wall modelled as a *plane* at lateral distance L (`d = L / sin|bearing|`, nearest at the
edge of the arc) with a 6 m open right side, `delta_deg = 0`:

| case | L | anchored | gapDirection | d_min | proximity | emitted frame |
|---|---|---|---|---|---|---|
| A | 1.2 m | true (placeholder scale) | 0.000 | 1.202 m | 0.749 | `steer 0.0, bearing 0.0, blocked false` |
| B | 1.2 m | **false (live)** | 0.2917 | — | 0.5 | `steer 0.2700, bearing 26.25, blocked false` |
| C | 0.40 m | true | 0.2708 | 0.401 m | 1.0 | `steer 0.5204, bearing 24.375, blocked false` |
| D | 0.40 m | **false (live)** | 0.3125 | — | 0.5 | `steer 0.2795, bearing 28.125, blocked false` |

Every frame carries `ttl_ms 400`, `source "x4_cloud"`, and the response's echoed `t_capture_ms`.

Case A is the sharp edge of spec bug 1: with the metric rule, a wall 1.2 m away is "passable"
(1.2 > `D_STOP_M`), the gap is the whole arc, and `steer` is **0.0** while `proximity` already
reads 0.75. The scale-free rule (B) steers away from the same wall, because its threshold
self-scales to the scene instead of sitting at a fixed 0.6 m.

## E. Spec bugs and deviations (report, do not paper over)

1. **`D_STOP_M` is used both as the passability threshold and as the ramp's saturation point, so
   the two halves of the law never co-vary.** Below 0.6 m `proximityFactor` is pinned at 1.0;
   above it every column is "passable" and `gapDirection` is 0. A wall **dead ahead at 1.2 m**
   therefore yields `blocked = false, steer = 0.0` on the metric path. Implemented as specified
   (the task is explicit) and pinned by
   `test_a_wall_dead_ahead_at_one_metre_is_not_blocked_by_this_law` so a fix is a visible change.
   The fix is a separate passability distance (~`D_CLEAR_M` scaled, or a time-to-contact rule),
   not a change to the ramp.
2. **`GapProfile` carries no fitted scale**, so §2.3's conversion cannot be performed by the
   bridge even in principle. `RELATIVE_DEPTH_SCALE_M = 1.8` is a documented placeholder derived
   from §4.5's raw span, and it drives nothing that runs live. A contract amendment is required
   (see the seam in §C). Also, one parameter is not enough — the fit needs scale *and* offset.
3. **Ruling 1 (±90) collides with a nonzero `delta_deg`.** The outermost column centre is at
   88.125°, so any |`delta_deg`| > 1.875 rotates part of the ring out of the forward arc, and
   `normalize_bearing_deg` would fold a bearing past +180 back to a *negative* one — silently
   putting the gap on the wrong side. The invariant is therefore enforced on the **ring**, not
   just on the emitted bearing (a 45° offset was measured emitting a legal-looking 69.375 from a
   ring reaching 133°), and such a frame is dropped with a warning. The plan should state that
   step 23 requires |`delta_deg`| ≤ 1.875, or ruling 1 needs widening to the rotated arc.

   **Fixed, per plan §3.3 ruling 1 (the ruling widened; this is the demo-critical one).** The
   invariant now reads the ring's **camera-frame** bearings — `yaw + (cx - 0.5) * FOV`, carried on
   every `RingColumn` as `camera_bearing_deg`, raw and never normalised — so ±90 is a claim about
   the *ring* and no mount offset can trip it. `|delta_deg|` is bounded instead, at
   `DELTA_DEG_MAX = 30` in `config.py`, which fails a crooked mount **once at startup** rather than
   per frame; the emitted `gap_bearing_deg` may therefore span ±(90 + |`delta_deg`|) ≤ 120, so the
   ±180 fold that would flip the gap's side is unreachable rather than guarded. Before the fix a
   scripted left wall emitted at `delta_deg` 0 and 1.875 and was **dropped at 2.0, 12, −25 and 30**
   ("the ring reaches 100.12 deg ... outside the +-90 forward arc"); after it, all six offsets emit
   and the bearing rotates rigidly (24.375 → 36.375 at `delta_deg` 12). `tests/bridge` +
   `tests/transport` = **552 passed, 1 skipped** (was 533/1).

   One judgement left open: 30° of offset means 30° of the wearer's forward arc is unseen on one
   side and the outermost bearing is 118.125°, i.e. behind their shoulder line. `gap_direction`
   already saturates at ±90 so the *cue* is bounded, and the number is honest geometry, but a
   startup warning above roughly 15° ("N degrees of your forward arc is unseen") would be worth
   more to whoever is straightening the camera than the hard failure at 30.
4. **§2.3's `range_band` cross-check is nearly vacuous.** Bands are near ≤ 5 m / mid 5–15 / far
   > 15, while steering lives at 0.6–3 m: everything in range is `near`, so only a `far` box over
   near depth (two bands) can ever invalidate the anchor. It can also false-positive — a far
   building visible above a near hedge in the same crop is physical, not a contradiction. Built
   as specified; worth narrower bands or a depth-vs-pinhole *metre* comparison later.
5. **The plan cites "step 8, S1"**, and `docs/steps/step-08-brief.md` has no S1 item. The rule
   itself (no silent fakes) is honoured throughout; the citation should be fixed or the brief
   should gain the label.
6. **Residual double-judgement.** Step 22 already rejects above `depth_max_ground_residual = 0.25`
   and reports `anchored`. The bridge's `GROUND_FIT_RESIDUAL_MAX = 1.0` deliberately does *not*
   duplicate that threshold — it only catches a fit that blew up while still claiming `anchored`.

## F. What is still unproven

* **No live X4 frame has been through this code.** Every ring in the tests is scripted. The plan's
  acceptance "the ring's bearings span −90…+90 on a live X4 frame" is step 25's.
* **`UNANCHORED_PASSABLE_FREEDOM` and `UNANCHORED_MIN_GAP_DEG` are unvalidated against reality.**
  They are defensible (harmonic midpoint; the most permissive metric width) but no walk has shown
  whether they call real doorways passable.
* **`RELATIVE_DEPTH_SCALE_M` is not a measurement** and the whole metric path is therefore
  untested against the world.
* **No timing.** Ring assembly, smoothing and the law are ~48-element pure-Python loops on the
  event loop thread inside `_handle_response`; expected to be tens of microseconds, but unmeasured.
* **The 5-second stats line does not carry the steer counts** — they are `BridgePipeline.steer_sent`
  / `.steer_undelivered` properties, deliberately kept off the line because other tools parse it.
* **Nothing consumes the frame yet.** Step 24 is the phone side; the numbers in §D are what it
  should be built against.
* **The `blocked`-on-a-flat-ring behaviour has not been seen in the field**, and if the venue
  produces low-contrast frames (a blank painted wall) it could over-block.
