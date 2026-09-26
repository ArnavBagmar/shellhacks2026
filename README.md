# Smart Cane — ShellHacks 2026

An assistive extension to the white cane. A chest-mounted iPhone detects front hazards on-device; a shoulder-mounted Insta360 X4 feeds a laptop that sends six dewarped crops to a cloud NVIDIA L4. Both produce small JSON `AwarenessEvent`s; the phone's feedback manager turns them into haptics and speech.

**Read first:** `docs/camera-sync-design.md` (the design), `docs/device-responsibilities.md` (who owns what), `docs/build-plan.md` (per-feature requirements, with corrections that override older text), `docs/orchestration-runbook.md` (the 19 build steps).

## Repository layout

| Path | What it is | Status |
|---|---|---|
| `contracts/` | The integration surface: `awareness_event.schema.json` (the 11-field contract, single source of truth), `transport_envelope.schema.json` (ping/pong/event frames), the golden fixture both test suites run, and a README with field semantics + the COCO→class mapping | **Done, frozen** — any change means re-running step 2 in both languages |
| `backend/` | Python 3.12 `uv` project, package `cane`: `cane.contracts` (Pydantic model + parser), `cane.transport` (NTP offset estimator, WebSocket event-link server, CLI), `cane.bridge` (X4 capture, six-crop dewarp, one-in-flight sender, health monitor + local fallback, dev stub server), `cane.cloud` (inference service: YOLO11s + ByteTrack + closing speed, FastAPI; L4 deploy files unverified), `cane.scene` (spoken questions answered by Gemini over the dewarped 360 crops) | **Done** (steps 2, 3, 6, 7, 8, 20) |
| `ios/CaneKit/` | SwiftPM package: `CaneContracts` (Codable model + validation), `CaneTransport` (WebSocket client, pong responder, reconnect, query/answer frames), `CaneFeedback` (feedback manager gauntlet), `CaneNavigation` (Routes/Places, route follower, turn cues) + `nav-cli`, `CaneOutput` (haptic alphabet, speech, earcon, output arbiter), `CaneVoice` (open mic, "Hey Cane" gate, intents), `CaneCamera` (shared front camera), `CaneAssist` (question → laptop → spoken answer) | **Done** (steps 2, 3, 9, 10, 12, 20) |
| `ios/CaneApp/` | The iPhone app (XcodeGen `project.yml`): composition root, setup screen, outward-facing "reactor" chest display | **Built**, not yet run on a device (step 20) |
| `docs/steps/` | Per-step build briefs and architect design reviews — far more detailed than the runbook; each is the binding spec for that step | Steps 2, 3, 6, 7, 8, 9, 10, 12 (see its README) |

## Quick start

```bash
# Python (needs uv; CPython 3.12 is fetched automatically)
cd backend && uv sync --extra mac && uv run pytest -q   # 605 tests (+1 live-X4 test behind CANE_LIVE_X4=1)

# Swift (Xcode 26 / Swift 6.3; tests run on the Mac, no device needed)
cd ios/CaneKit && swift test                       # 346 tests, includes real phone↔laptop event and query/answer round trips

# The phone app (needs xcodegen; set your team in ios/CaneApp/Config/Local.xcconfig first)
cd ios/CaneApp && xcodegen && open CaneApp.xcodeproj

# "Hey Cane, is there a bathroom around?" -- laptop side, over a still image (add --scene to cane.bridge for the live X4)
cd backend && uv sync --extra mac --extra scene
cd backend && GEMINI_API_KEY=... PYTHONPATH=src uv run python -m cane.scene --serve --equirect ../docs/steps/assets/step-06-live-equirect.jpg

# Run the laptop-side event link and watch the clock offset (the phone app connects to ws://<laptop>:8765)
cd backend && PYTHONPATH=src uv run python -m cane.transport.server --port 8765 --demo-event-every 1

# Drive the X4 bridge live against the dev stub (camera in USB webcam mode, 2880x1440)
cd backend && PYTHONPATH=src uv run python -m cane.bridge.dev_stub_server --port 8000 &
cd backend && PYTHONPATH=src uv run python -m cane.bridge --source x4 --rung 1 --inference-url http://127.0.0.1:8000/infer --allow-unsynced

# Real YOLO11s locally (separate env with torch; first /warmup takes ~11 s on Apple MPS)
cd backend && UV_PROJECT_ENVIRONMENT=.venv-yolo uv sync --frozen --extra yolo --extra mac
cd backend && UV_PROJECT_ENVIRONMENT=.venv-yolo CANE_DETECTOR=ultralytics CANE_DEVICE=mps CANE_WEIGHTS_PATH=yolo11s.pt PYTHONPATH=src uv run --extra yolo uvicorn cane.cloud.app:app --port 8000
```

## Where each lane starts

- **P1 · perception (iOS)** — step 4 in `docs/orchestration-runbook.md`. Produce `AwarenessEvent(source: .phone)` and send it through `CaneTransport`. Bearings are torso-relative and must be normalized into `(-180, 180]`; the phone needs a frame-stable `track_id` (see `docs/steps/step-09-design.md` hazard H1). Add new Swift targets to `ios/CaneKit`, never a sibling package.
- **P2 · X4 bridge + cloud (Python)** — steps 6, 7 and 8 are built and reviewed. Remaining: deploy the service to the L4 once the GPU quota lands (`backend/deploy/README.md`, then `python -m cane.cloud.smoke --url http://<vm>:8000`), Firestore writes (15) and the latency benchmark (16). The prototype capture code it references lives in the separate `insta360` folder documented in `docs/insta360feed.md`.
- **P3 · experience (iOS)** — steps 9, 10, 12 and the phone app (step 20: chest display, open mic, "ask the 360 view" via Gemini) are built; see `docs/steps/step-20-brief.md` for how to run it and what is still to verify on the device. Open: on-phone sign reading (13), the haptic acceptance harness, phone → laptop `health` frames.
- **P1 → the app** — the front camera is already running in `CaneCamera.FrontCamera` (mirroring, Center Stage and stabilization off). Register a `FrameConsumer` for YOLO and feed `AwarenessEvent(source: .phone)` to the app's `FeedbackManager` in `ios/CaneApp/Sources/Runtime.swift`.

## Rules that keep three lanes from colliding

1. `urgency` is never on the wire; producers emit facts, the phone computes urgency.
2. The X4 path never states metres — `range_m` is null unless `source == "phone"`. All three validators enforce it.
3. All 11 keys are always present (nullable ones as explicit `null`); unknown keys are rejected.
4. The phone is the master clock; the cloud echoes `t_capture_ms` and never reads its own clock.
5. Commits: `<type>: <description>` (feat / fix / test / docs / chore). Tests first.
