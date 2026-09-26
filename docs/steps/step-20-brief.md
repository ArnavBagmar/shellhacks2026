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

`query` phone → laptop: `query_id`, `kind` ∈ `find | describe_around | describe_front | ask`, `target`
(required iff `find`), `utterance`, `t_phone_ms`, `phone_frame` {`jpeg_b64`, `hfov_deg`, `width`,
`height`} | null. `answer` laptop → phone: `query_id`, `status` ∈ `found | not_found | described |
unavailable`, `subject`, `bearing_deg` (only with `found`, in `(-180, 180]`), `detail`, `confidence`,
`latency_ms`, optional `phone_box` (below). All other keys always present; unknown keys rejected. Golden fixture:
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

**`ask` (any other question).** The phone keeps a few commands local (stop, the time, help,
directions) and the fixed ones above; every other sentence of two or more words goes out as
`kind: ask` with the phone photo, and the phone says "Let me check." instead of "hold still". The
laptop sends all views plus the question, the local date and time and `CANE_LOCATION` (optional,
e.g. `FIU, Miami, Florida`), and asks for at most 30 spoken words, surroundings first. Gemini
answers `found` (one visible thing → image + x → bearing, spoken direction-first like a find) or
`answered` (→ `described`, spoken as given). Only `ask` tries Google Search grounding
(`CANE_GEMINI_SEARCH`, default on); a refused search is retried at once without it and search
rests 10 min. Live 2026-09-26: the key got 429 for search on every try (plain calls fine), so
answers came from the images and the model alone — weather → "I can't check the live forecast,
but you are indoors…", "do you see a door nearby" → "No doors are visible right here…", "capital
of France" → "Paris is the capital of France." — 1.5–1.7 s each.

**Weather (`backend/src/cane/scene/weather.py`).** Because search is refused on the demo key, a
weather-sounding `ask` ("how's the weather", "do I need an umbrella", "is it cold out") makes the
laptop fetch Open-Meteo (free, no key) while the crops are cut, and puts one sentence of live
conditions into the prompt; search is then skipped. `CANE_LOCATION` is geocoded by trying each
comma part filtered by the parts after it ("FIU, Miami, Florida" → Miami, FL); `CANE_LAT_LON=lat,lon`
skips geocoding, `CANE_TEMP_UNIT=celsius`, `CANE_WEATHER=off`. Cached 10 min, 3 s timeout, any
failure → no facts. Live 2026-09-26: "how's the weather outside" → "It is eighty-five degrees and
mainly clear outside." (2.9 s); "do I need an umbrella today" → "Nope, you're good. Today's weather
shows only a fourteen percent chance of rain."

**Natural voice (`backend/src/cane/scene/voice.py`, `CaneOutput/NaturalSpeechRenderer.swift`,
`CaneAssist/LaptopVoice.swift`).** Answers and prompts (the arbiter's `.answer` and `.prompt`
channels) are spoken in an ElevenLabs voice; hazards and turn cues never are -- they stay on
`AVSpeechSynthesizer`, instant and offline. The phone sends `speak {speak_id, text}`; the laptop
(key in `backend/.env` as `ELEVENLABS_API_KEY`, never on the phone) replies with exactly one `voice
{speak_id, status, format, audio_b64, latency_ms}` -- `unavailable` at once when it has no voice, so
the phone never waits. The phone plays the MP3 with `AVAudioPlayer` (resume-once, watchdog at clip
length + 1.5 s) and uses its own voice if no clip arrives within 2.5 s, the clip will not play, or the
laptop said `unavailable` in the last 30 s. Both sides cache by text; the laptop pre-synthesizes the
five common prompts at start and starts a described answer's audio before sending the answer.
Defaults picked by ear: Sarah (`EXAVITQu4vr4xnSDxMaL`) on `eleven_turbo_v2_5`, `mp3_44100_64`;
override with `CANE_ELEVENLABS_VOICE` / `CANE_ELEVENLABS_MODEL`, `CANE_VOICE=off`, or the phone's
"Natural voice for answers" switch. At most 2 requests in flight (a burst of 5 got `429
concurrent_limit_exceeded`), one retry on that refusal. Live 2026-09-26: new phrase 0.28 s laptop
round trip, cached 1-2 ms; Turbo 0.26 s, Flash v2.5 0.3-0.55 s, v3 Conversational 1.0 s.

Live runs (2026-09-26): cold client, 6 × 640 px, thinking LOW → timed out at 8 s. Then MINIMAL → 400 from
3.8-flash, fallback `gemini-3.5-flash-lite` answered in 1.1 s (1.6 s end to end, found "doorway" at +14°). Then 3.8-flash at LOW: 6.0 s to warm, 5.7 s to answer, not_found → lite
became the primary. Next lite run sat 9 s with no reply (same settings) → hedged requests. Three hedged runs: the first copy never answered, the second answered in 1.4-2.6 s → two copies at t=0.

### "How far is the Sprite can?" (TrueDepth distance)

"how far is / how far away is / how close is / distance to X" -> `VoiceIntent.howFar(X)` -> an ordinary
`find` query. Gemini's reply carries `box_2d` ([ymin, xmin, ymax, xmax], 0-1000, its native format); when
the thing is in the **phone photo** the laptop sends it as `answer.phone_box` (fractions, top-left
origin; a box in a 360 view is dropped -- no depth there). The prompts ask Gemini to answer with the
phone image when the thing is visible in it.

The phone keeps the whole TrueDepth map taken with the question's photo (`FrontCamera.snapshotJPEG(withDepth:)`,
turned to the photo's orientation: the depth connection is rotated like video where supported, the rest
in software), not a live one -- the answer comes back ~1.5 s later. `DepthMap.distance(inBox:)` reads the
inner 60 % of the box and takes the 30th percentile of valid pixels (leans to the object, not the wall
behind a loose box). Spoken only when `isTrustworthy()` (absolute accuracy, >= 35 % coverage, 0.2-3 m),
in feet for US/UK locales: "Slightly left, sprite can, about 4 feet away, on the table." Asked but not
measurable: past 3 m -> "more than 10 feet away"; found only in a 360 view -> "Face it and ask again to
measure how far."; no sensor / sunlight -> says so. A measured distance is also added to plain finds; the
failures only when asked. Every outcome is logged to the phone black box as `distance`
(`outcome`, `metres`, `coverage`, `accuracy`). Live check 2026-09-26: Gemini returned tight boxes for
"door" and "chair" on a test photo (1.2-2.4 s). **Not yet checked on the phone**: that the rotated depth
map lines up with the photo (hold a can at a taped 1 m and 2 m, left and right of centre).

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
4. "How far is the can?" with a can at a taped 1 m, then 2 m, left then right of centre: spoken distance
   within ~15 %, and the phone log's `distance` record says `measured`.
5. Step 10's 24-trial haptic protocol (step-10 design Amendment 7) — the `HapticTrial` harness is
   **not built yet**; the self-test ("Play the haptic alphabet") is.

## G. Not done in this step

- Phone → laptop `health` frames (the laptop's fallback works without them; advisory only).
- Step 13 on-phone OCR: "read that sign" currently asks the laptop to describe the phone photo.
- `HapticTrial` acceptance harness (step-10 design Amendment 7).
- Step 9 rounds `.aboutMetres` to nearest; step-10 design R2 asks for floor (step 10 renders `n < 1` as "close").
