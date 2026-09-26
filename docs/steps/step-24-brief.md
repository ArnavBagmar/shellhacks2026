# Step 24 brief — The phone acts on `steer`: integrator, navigation blend, failsafe decay

Written after implementation, 2026-09-26. Binding spec: `docs/shepherd-steering-plan.md`
§0–§4, §3.1 and its Step 24; `docs/steps/step-09-design.md` and `step-10-design.md` for
the two modules this lands in. Step 21's `SteerCommand` / `SteerLimits` were taken as
given and consumed, not redesigned. Nothing was committed.

Shepherd's stages 6 (navigation merge), 8 (motor response) and 9 (safety failsafe), with
a wearer where they had a motor.

## A. What was built

```
laptop --steer--> EventLinkClient.steerCommands --> FeedbackManager.ingest(SteerCommand)
                                                        │  StalenessGate, 400 ms
                          100 ms tick (already existed) ─┤
                                                        ├─ SteerBlend  (stage 6)
                                                        ├─ SteerIntegrator (stage 8)
                                                        └─ decay to neutral (stage 9)
                                                             │
                                          SteerCue --> SteerCueOutput --> OutputArbiter
                                                                          .steering (lowest)
```

### New files

| file | what it is |
|---|---|
| `ios/CaneKit/Sources/CaneFeedback/SteerPolicy.swift` | plan §3's phone rows in one place, plus the two mappings (quiet gate, pulse rate) |
| `ios/CaneKit/Sources/CaneFeedback/LeakyIntegrator.swift` | stage 8 and stage 9 as one scalar value type; no clock, no actor |
| `ios/CaneKit/Sources/CaneFeedback/SteerIntegrator.swift` | the two integrated channels (lateral, proximity) |
| `ios/CaneKit/Sources/CaneFeedback/SteerBlend.swift` | stage 6's `steer + bias·(1 − proximityFactor)`, and `NavigationBias` with its expiry |
| `ios/CaneKit/Sources/CaneFeedback/SteerPulseClock.swift` | "pulse every N ms" → "fire on this tick, or not" |
| `ios/CaneKit/Sources/CaneFeedback/SteerCue.swift` | the A13-style seam: `SteerCue`, `SteerCueSink`, `NoSteerCueSink` |
| `ios/CaneKit/Sources/CaneFeedback/SteerStaleness.swift` | `StalenessGate` extended to `SteerCommand` — the **existing** predicate, not a second one |
| `ios/CaneKit/Sources/CaneFeedback/SteeringState.swift` | everything step 24 remembers, as a value |
| `ios/CaneKit/Sources/CaneFeedback/Steering.swift` | the pure stage: `accept`, `setLink`, `bias`, `advance` → `(state, Effect)` |
| `ios/CaneKit/Sources/CaneOutput/SteerHaptic.swift` | `HapticScript.steering(userGain:)` and the steering gain tier |
| `ios/CaneKit/Sources/CaneOutput/SteerCueOutput.swift` | `SteerCue` → (pattern, pan) → the arbiter; the plan is a tested value |
| `ios/CaneKit/Sources/CaneOutput/OutputArbiter+Steering.swift` | `steerPulse` / `cancelSteering`, built out of the existing `say` |

### Edited files

| file | change |
|---|---|
| `ios/CaneKit/Package.swift` | `CaneFeedback` now depends on `CaneTransport` (see §D.1) |
| `ios/CaneKit/Sources/CaneFeedback/FeedbackManager.swift` | `steerSink`, `ingest(SteerCommand)`, `setLinkUp`, `setNavigationBias`; `tick` also advances the integrator |
| `ios/CaneKit/Sources/CaneFeedback/FeedbackState.swift` | holds a `SteeringState` |
| `ios/CaneKit/Sources/CaneFeedback/FeedbackCounters.swift` | six steering counters, deliberately apart from the event tallies (§D.5) |
| `ios/CaneKit/Sources/CaneTransport/EventLinkClient.swift` | `steerCommands` stream, `InboundSinks.deliver`, buffer of 1 |
| `ios/CaneKit/Sources/CaneTransport/EventLinkClientRunLoop.swift` | the `steer` case now delivers instead of logging "not yet consumed" |
| `ios/CaneKit/Sources/CaneOutput/HapticScript.swift` | `HapticSymbol.steerTick` + its one-element row in the table (§D.2) |
| `ios/CaneKit/Sources/CaneOutput/OutputArbiter.swift` | `OutputChannel.steering`, below everything (§D.3) |
| `ios/CaneApp/Sources/Runtime.swift` | the steer ingest loop, the `SteerCueOutput` sink, link state from the connection poll |
| `ios/CaneApp/Sources/NavigationController.swift` | reports a lateral bias for `.now` / `.correctHeading` cues |

Untouched: `backend/`, `contracts/`, every other `docs/` file, and every pre-existing
test.

## B. The cue, and why it cannot be mistaken for anything else

The five hazard letters and the two navigation shapes are all **bursts** whose *shape*
is the message: `.ahead` a 180 ms buzz, `.left` two clicks 200 ms apart, `.right` three,
`.behind` a 320 ms swell, `.alarm` a 900 ms rattle, navigation the `.left`/`.right`
shapes one gain tier down. The steering cue is the opposite kind of signal:

| | steering cue | hazard / navigation |
|---|---|---|
| elements per emission | **one** | 1–9 (counting them is the message) |
| length | 20 ms (shortest in the alphabet) | 180–900 ms |
| sharpness | **0.20** — a dull thud | 0.55 clicks / 0.15–0.35 buzzes |
| intensity | **0.25–0.45**, fixed | nav 0.35–0.63, hazard 0.36–1.00 |
| what carries the meaning | **repetition rate** (200–700 ms) and **stereo side** | the pattern itself |
| speech | never | urgency 2–3 always, nav always |

So the wearer's model stays one sentence longer: *a soft tick that keeps repeating is the
gap — the faster it ticks the closer the thing is, and it comes from the side you should
move toward.* Nothing is counted, nothing is decoded. `HapticAlphabet.validate()` covers
the new row, which is how "distinct from every existing pattern" is *proven* (no two
scripts in the alphabet share elements) rather than asserted, and a test pins that
`HapticSymbol.symbol(level:sector:)` can never select it, so it can never stand in for a
warning.

Gain ladder, asserted at every user gain in `HapticAlphabet.userGainRange`:
**steering < navigation < the softest hazard.** The steering tier sits *below*
`HapticAlphabet.intensityFloor` (0.35) on purpose: that floor exists because step 9
writes a `TrackRecord` at presentation start, so an unfelt urgency-1 cue suppresses the
next warning for eight seconds — a steering pulse records nothing and another follows
within 700 ms.

### Quiet threshold

`quietProximity = 0.35`, releasing at `0.30` (hysteresis, same reason `Hysteresis` exists
for urgency). On §2.3's ramp (`d_clear` 3.0 m, `d_stop` 0.6 m) that is **2.16 m** — about
1.5 s of walking at 1.4 m/s, three cane sweeps out — with the cue hanging on until
2.28 m. Below it **nothing is emitted at all**: no pulse, no earcon, no counter beyond
the ones that say a frame arrived. Plan risk 3 is the whole reason: this is the third
channel after hazards and navigation, and a chatty one ruins the demo.

### Pulse rate

700 ms at the threshold → 200 ms at the stopping distance, continuous in between. The
cue can only fire on a tick, so the felt rate is quantised to 100 ms (a 250 ms interval
is felt as 300 ms); that is accepted, the rate is a gradient and not a measurement. When
`proximity_anchored` is **false** the rate is quantised to **three bands** instead of a
ramp — plan §2.3 says an unanchored proximity is a band and not a number, and rendering
it as a continuous rate would be claiming a metre we do not have.

## C. The numbers

### Integrator, `INTEGRATOR_K = 0.35` per 100 ms

| fraction of a step change | time |
|---|---|
| 35% | 100 ms (one tick) |
| 50% | 161 ms |
| 63% (time constant) | 231 ms |
| **90%** | **534.5 ms — the 6th tick** |
| 99% | 1069 ms |

**Is 0.35 right?** It is defensible and it is what ships, but it is on the slow side and
the plan's "tuned in step 24" could not be honoured on a wearer without the rig. The case
for keeping it: the wearer notices *onset*, not settling, and onset is 35% inside one
tick and half the step inside 161 ms; a twitchy cue on a 3 Hz signal with a soft depth
anchor is worse than a slightly laggy one. The case against: 534 ms is 0.75 m of walking
at 1.4 m/s, and `d_stop` is 0.6 m, so a step change that appears at the stopping distance
is still arriving as the wearer reaches it. If step 25 shows it lagging, `k = 0.5` puts
90% at 332 ms and needs no other change. **Recommendation: leave 0.35, re-judge on the
walk, and treat the number as measured-not-tuned in the pitch.**

The maths is elapsed-time-correct — `k_eff = 1 − (1 − k)^(elapsed/100)` — for two
reasons: a stuttering tick must not retune the filter, and `k_eff` is then *structurally*
in `[0, 1)`, which is what makes overshoot impossible rather than merely clamped. With
the naive `k·elapsed/100`, one long tick (the app was backgrounded) would ring straight
past the target. A test asserts no overshoot and no reversal for every sign of step and
tick lengths from 1 ms to 5 s.

### Failsafe, `FAILSAFE_DECAY_MS = 500`

A fixed **rate** (full scale per 500 ms), not an exponential: an exponential never
reaches zero, and "the cue stopped" has to be a fact at a known instant. Both channels
decay together, so the cue fades out centred and slowing. From full scale the pulse rate
crosses the release threshold at **350 ms** and the integrator is exactly neutral at
500 ms — silence arrives before neutrality, which is the safe order. The decay clock
starts on the first tick *after* expiry, so it can finish up to one tick early, never
late.

## D. Where the plan was wrong, silent, or had to be interpreted

1. **Module boundary.** §2.4 says step 24 lives in `Cane{Feedback,Output}`, but
   `SteerCommand` and `SteerLimits.ttlMs` live in `CaneTransport` (step 21), and the
   instruction was to consume them rather than redefine them. `CaneFeedback` therefore now
   depends on `CaneTransport`. No cycle (`CaneTransport` depends only on `CaneContracts`),
   and the alternative — a parallel steer value type inside `CaneFeedback` — would have
   put the 400 ms TTL in two files.
2. **`HapticSymbol` gained a sixth case, and step-10 design Amendment 1 says "the
   alphabet has exactly five symbols".** This needs the step-10 owner's eyes. The
   argument: the five-symbol limit is about single-site discrimination of *messages the
   wearer decodes*, and `steerTick` is not one — it is a continuous state signal like a
   reversing sensor, never selected by `symbol(level:sector:)` (tested), never spoken, and
   the shortest and weakest thing in the alphabet. Putting it *inside* the validated
   alphabet is what proves it collides with nothing. If the owner disagrees, the fix is a
   separate script type and a `HapticScript.symbol` that can hold either family.
3. **`OutputChannel` gained `.steering` as the new lowest case**, which shifts the
   implicit raw values. Nothing persists or transmits an `OutputChannel` (checked across
   `CaneKit`, `CaneApp` and the test targets); every comparison is relative. Speech
   arbitration is byte-for-byte unchanged: hazard still preempts everything, navigation
   speech under a hazard is still dropped rather than queued, and the 1800 ms post-hazard
   speech gap now also silences the steering cue — which is wanted. Two new tests pin all
   three so step 24 cannot regress them.
4. **§3's table has no row for the quiet threshold, the pulse-rate range, or how long a
   navigation bias lives.** Step 24 chose 0.35/0.30, 700→200 ms and 3 s (see §B and
   `SteerPolicy`). They belong in the plan's §3 table; this brief is the only place they
   are written down today.
5. **A stale `steer` is counted, but not into `staleDrops`.** The brief says "dropped and
   counted, exactly as a stale event is", and step-9 H4 says `staleDrops` is the one signal
   the §3.4 transport fallback reads. Summing steering staleness into it would flip the
   phone to local inference because the *depth head* was slow. So there are six separate
   counters (`steerReceived`, `steerStaleDrops`, `steerOutOfOrder`, `steerExpired`,
   `steerFailsafes`, `steerPulses`), mirroring the existing `staleDrops` vs `queueExpired`
   distinction: `steerStaleDrops` is "arrived late", `steerExpired` is "aged out while
   held".
6. **"stereo pan = steer angle" — `steer` is not an angle.** It is a dimensionless
   −1…+1 that already has `proximityFactor` folded into its magnitude. Mapped
   `±1 → ±90°`, the edge of the forward ring §3 defines, and fed through
   `StereoPan.pan(bearingDeg:route:channelCount:)` so the cap, the route gate and the
   sign convention are the ones a hazard uses. `gap_bearing_deg` was the other candidate
   and is deliberately unused: the plan says the pan tracks `steer`, and `steer` is what
   the integrator smooths.
7. **`blocked` has nowhere to go.** §3.1 ruling 2 makes `blocked` mean "do not trust this
   as a path", which is a *message*, and a sixth message is exactly what Amendment 1
   forbids. Step 24 renders it not at all. Recommendation: step 26's voice layer owns it
   ("the left side is blocked"), which is where a sentence belongs.
8. **The quiet gate bounds the navigation blend.** Because the cue is only audible at
   proximity ≥ 0.35, a navigation bias is never *rendered* at more than 0.65 of its value,
   and with nothing close it moves the integrator but is heard by nobody. This is
   intentional — navigation already owns a speech channel, and a steering cue that spoke up
   for turns would be the chatty third channel — but it does mean Shepherd's stage 6 is
   weaker here than in their always-on motor. Worth saying out loud in the pitch rather
   than claiming a full merge.
9. **Link loss is coarser than staleness.** The app learns the socket dropped from the
   1 s connection poll; the 400 ms TTL is what actually bounds the failsafe in practice.
   A frame arriving is treated as proof the link is up, so a lagging poll cannot leave a
   live command unrendered.

## E. Tests

48 new tests; `swift test` in `ios/CaneKit` runs **433, 0 failures** (385 before).

| acceptance criterion | test |
|---|---|
| 1. 90% of a step in the tuned time, never overshoots | `LeakyIntegratorTests.testAStepChangeReachesNinetyPercentOnTheSixthTick`, `…testItNeverOvershootsOrReversesForAnyStepOrTickLength`, `SteeringManagerTests.testAStepChangeInSteerIsRenderedGraduallyAndNeverOvershoots` |
| 2. stale → silent within 500 ms, monotonic, nothing held | `SteeringManagerTests.testAStaleSteerDecaysToSilenceWithinFiveHundredMilliseconds`, `LeakyIntegratorTests.testDecayReachesNeutralInFiveHundredMillisecondsAndStops` |
| 3. nav bias zero at `proximityFactor == 1`, full at 0, continuous between | `SteerBlendTests` (4 tests), `SteeringManagerTests.testANavigationBiasPullsTheCueOnlyWhileNothingIsClose` |
| 4. hazards still preempt everything | `SteerArbitrationTests.testHazardSpeechStillPreemptsEverythingAndNavigationIsStillDropped`, `…testAHazardCutsASteeringPulseInFlight`, `…testTheSteeringCueStaysOutOfTheGapAfterHazardSpeech`, `…testTheChannelOrderPutsSteeringBelowEverythingIncludingNavigation` |
| 5. nothing emitted below the quiet threshold | `SteeringManagerTests.testNoCueIsEmittedAtAllBelowTheQuietThreshold`, `SteerPolicyTests` (2 tests) |
| 6. link loss triggers the same decay | `SteeringManagerTests.testLinkLossTriggersTheSameDecayImmediately`, `…testTheLinkComingBackDoesNotResumeACueWithoutANewFrame` |
| 7. full suite green, pre-existing tests untouched | 433/0; no existing test file was edited |

Worth naming separately: the TTL gate is tested at the boundary (exactly at TTL is still
true, one millisecond past is dropped, a generous producer TTL is clamped down to plan
§3's 400 ms); out-of-order frames cannot undo a newer command; `ingest` alone never moves
the integrator, so a reconnect flushing a backlog cannot jump the cue; the steering path
touches none of the announcement counters; and `SteerDeliveryTests` proves the new stream
keeps only the newest command and does not disturb the event or answer streams.

The first RED was a compile failure across all four new test files (`cannot find type
'SteerCueSink'`, `'SteerCue'`, `InboundSinks has no member 'deliver'`). The second, after
the types existed with the integrator maths deliberately neutralised (`advanced` returning
the target, `decayed` returning itself), was 14 assertion failures on exactly the
acceptance criteria — `("1.0") is not equal to ("0.35")` for the step response and
`("Optional("pulse")") is not equal to ("Optional("silence")") - nothing may be emitted
after the cue stops` for the failsafe.

## F. Unproven — what step 25 has to find out

1. **Whether a 0.20-sharpness, 0.25–0.45-intensity single tick is felt at all** through a
   shirt at the sternum, and whether it is felt *less* than a turn cue rather than just
   differently. The whole three-tier ladder is arithmetic today; no wearer has felt it.
2. **Whether 200–700 ms reads as a gradient** or as two states (fast/slow). If it reads as
   two states, the honest fix is to render the bands and stop claiming a ramp.
3. **Whether 2.16 m is the right onset distance.** Too early and the cue is on for the
   whole walk; too late and it is a confirmation rather than a warning. Easiest thing to
   tune on the walk, and it changes one constant.
4. **The earcon is the same 1.2 kHz tick a hazard uses**, only repeated and panned. A
   lower-pitched steering earcon would be more clearly a third voice, but
   `EarconRenderer.play(pan:)` takes no frequency and changing that protocol was out of
   scope. If the two sounds are confusable on the walk, that is the change to make.
5. **Steering pulses and hazard haptics share one Taptic Engine.** The arbiter serialises
   them, so in principle a pulse is cut the instant a hazard starts; whether the transition
   *feels* clean, especially inside the 1800 ms post-hazard gap, is unmeasured.
6. **A device with no Taptic Engine gets no steering cue at all** — deliberately not
   spoken. Fine on the demo iPhone; it means the simulator and an iPad show nothing.
7. **The `CaneApp` wiring is parse-checked, not compiled.** `CaneKit` is fully tested, but
   the app target is generated by `xcodegen` and was not built in this step; the first
   device build is the first real check of `Runtime` and `NavigationController`.
8. **Nothing above has seen a real `steer` frame**, because steps 22–23 are being built in
   parallel. Every number in §C is arithmetic on the plan's constants, and the end-to-end
   latency from `t_capture` to cue onset — the figure the pitch compares against Shepherd's
   <150 ms — is step 25's to measure.
