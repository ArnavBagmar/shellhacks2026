# Step 20 brief — the phone app: chest reactor, open mic, "ask the 360 view"

**Status:** built 2026-09-26 on `feat/phone-app` · owner: P3 (experience iOS)
**Plan:** `~/.claude/plans/look-into-the-build-plan-md-stateless-allen.md` (approved)

## A. What the user asked for

The iPhone is strapped to the chest, **screen facing out** (an "arc reactor"). The forward-looking
camera is therefore the **front (selfie) camera**. The mic is **always open**, gated by a wake word.
Headline flow:

> "Hey Cane, is there a bathroom around?" → phone: "Hold still, looking around." → laptop takes the
> freshest X4 360 frame, dewarps it into the six 60° crops (+ the phone's front photo), asks Gemini →
> `answer` frame → phone: "To your right, restroom sign, past the elevators."

## B. Decisions (binding)

| # | Decision | Why |
|---|---|---|
| B1 | Wake word "Hey Cane" (ASR variants Kane/Cain/…); "Hey Cane" alone arms the next utterance for 6 s | open mic in a loud hall must not act on bystanders or its own TTS |
| B2 | Gemini runs on the **laptop** (`cane.scene`), key from `GEMINI_API_KEY`, never in the app | the X4 crops live there; the key never ships on a device |
| B3 | The phone owns one shared front-camera session (`CaneCamera.FrontCamera`); P1's detector plugs in as a `FrameConsumer` | one `AVCaptureSession`, three users (perception, questions, signs) |
| B4 | Mirroring **off**, Center Stage **off**, stabilization **off** | a mirrored selfie frame flips every bearing's sign; the other two crop/pan and break FOV math |
| B5 | Direction travels only as `bearing_deg`; the phone speaks it with the same §C sector words as hazards | one vocabulary for the wearer; Gemini is told never to use direction words |
| B6 | `OutputArbiter` priority: hazard > answer > prompt > navigation. Hazards preempt everything; nothing below starts inside 1800 ms after hazard speech; navigation is dropped, never queued | runbook "Demo decisions" #2, step-10 design R1 |
| B7 | Mic muted while the phone speaks + 300 ms tail | no AEC (step-10 design H-11b) |
| B8 | Every failure is spoken: no link → "I can't reach the laptop", no answer in 20 s → "Sorry, that took too long", VLM failure → `status: unavailable` | step 14 acceptance: never hang |

## C. Wire shapes (`contracts/transport_envelope.schema.json`, additive)

`query` phone → laptop: `query_id`, `kind` ∈ `find | describe_around | describe_front`, `target`
(required iff `find`), `utterance`, `t_phone_ms`, `phone_frame` {`jpeg_b64`, `hfov_deg`, `width`,
`height`} | null. `answer` laptop → phone: `query_id`, `status` ∈ `found | not_found | described |
unavailable`, `subject`, `bearing_deg` (only with `found`, in `(-180, 180]`), `detail`, `confidence`,
`latency_ms`. All keys always present; unknown keys rejected. Golden fixture:
`contracts/fixtures/transport_query_answer.golden.json`, run by both suites. Server `max_size` 4 MiB.

## D. Laptop timing (`backend/src/cane/scene/service.py`)

settle 400 ms (the wearer is being told to hold still) → first X4 frame after that → six 512 px q75
crops (describe_front: the phone photo only, or crops C5/C0/C1 without one) → Gemini
(`gemini-3.5-flash-lite`, JSON schema, its default thinking, medium image detail, function calling off; two copies
go out at once, `gemini-3.8-flash` at thinking LOW joins after 3 s with no reply; first reply wins, 15 s cap) → bearing = crop yaw +
(x − 0.5)·60 + `delta_deg`, or (x − 0.5)·hfov on the phone photo. A newer query cancels the one in
flight. The client is warmed with one tiny call at start-up. Knobs: `CANE_GEMINI_MODEL`,
`CANE_GEMINI_FALLBACK_MODEL` (`none` disables), `CANE_GEMINI_HEDGE_S` (default 3), `CANE_GEMINI_PARALLEL` (default 2), `CANE_GEMINI_THINKING`, `CANE_GEMINI_MEDIA_RES`,
`--vlm-timeout-s`. The phone says "Still looking." at 7 s and gives up at 20 s.

Live runs (2026-09-26): cold client, 6 × 640 px, thinking LOW → timed out at 8 s. Then MINIMAL → 400 from
3.8-flash, fallback `gemini-3.5-flash-lite` answered in 1.1 s (1.6 s end to end, found "doorway" at +14°). Then 3.8-flash at LOW: 6.0 s to warm, 5.7 s to answer, not_found → lite
became the primary. Next lite run sat 9 s with no reply (same settings) → hedged requests. Three hedged runs: the first copy never answered, the second answered in 1.4-2.6 s → two copies at t=0.

## E. Run it

```bash
# laptop, no X4, no phone: one question over the repo's still equirect
cd backend && uv sync --extra mac --extra scene
GEMINI_API_KEY=... PYTHONPATH=src uv run python -m cane.scene --equirect ../docs/steps/assets/step-06-live-equirect.jpg --find door

# laptop, live X4 (USB webcam mode), no phone; saves what Gemini saw under views/<query_id>/
GEMINI_API_KEY=... PYTHONPATH=src uv run python -m cane.scene --source x4 --find door --save-views views

# laptop serving the phone over the still image (no X4); --source x4 works here too
# (the repo still is a poor test: half resolution, camera on a table, a bottle blocking the front)
GEMINI_API_KEY=... PYTHONPATH=src uv run python -m cane.scene --serve --equirect ../docs/steps/assets/step-06-live-equirect.jpg

# the full bridge with the X4, answering questions too
GEMINI_API_KEY=... PYTHONPATH=src uv run python -m cane.bridge --source x4 --rung 1 --inference-url http://<cloud>:8000/infer --scene

# phone app
cd ios/CaneApp && cp Config/Local.xcconfig.example Config/Local.xcconfig   # set DEVELOPMENT_TEAM + CANE_BUNDLE_ID
xcodegen && open CaneApp.xcodeproj                                          # run on the iPhone
```

Join the laptop to the iPhone's hotspot, put the laptop's address in the app's setup screen
(default `172.20.10.2:8765`), tap **Start walking**, strap on. Triple-tap the chest screen for a typed
command box and a status log; long-press 2 s (or two-finger double-tap) stops.

## F. Acceptance still to run on hardware

1. Say "Hey Cane, is there a bathroom around?" with a restroom sign taped on the **left**, then on the
   **right**: the spoken side must match both times (catches a mirrored or rotated frame).
2. A planted hazard mid-answer: the hazard cuts the answer, the answer is not replayed late.
3. Query → spoken answer ≤ ~3 s after the end of speech on the venue hotspot (laptop logs `latency_ms`).
4. Step 10's 24-trial haptic protocol (step-10 design Amendment 7) — the `HapticTrial` harness is
   **not built yet**; the self-test ("Play the haptic alphabet") is.

## G. Not done in this step

- Phone → laptop `health` frames (the laptop's fallback works without them; advisory only).
- Step 13 on-phone OCR: "read that sign" currently asks the laptop to describe the phone photo.
- `HapticTrial` acceptance harness (step-10 design Amendment 7).
- Step 9 rounds `.aboutMetres` to nearest; step-10 design R2 asks for floor (step 10 renders `n < 1` as "close").
