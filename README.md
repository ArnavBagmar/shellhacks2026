# Smart Cane — ShellHacks 2026

An assistive extension to the white cane. A chest-mounted iPhone detects front hazards on-device; a shoulder-mounted Insta360 X4 feeds a laptop that sends six dewarped crops to a cloud NVIDIA L4. Both produce small JSON `AwarenessEvent`s; the phone's feedback manager turns them into haptics and speech.

**Read first:** `docs/camera-sync-design.md` (the design), `docs/device-responsibilities.md` (who owns what), `docs/build-plan.md` (per-feature requirements, with corrections that override older text), `docs/orchestration-runbook.md` (the 19 build steps).

## Repository layout

| Path | What it is | Status |
|---|---|---|
| `contracts/` | The integration surface: `awareness_event.schema.json` (the 11-field contract, single source of truth), `transport_envelope.schema.json` (ping/pong/event frames), the golden fixture both test suites run, and a README with field semantics + the COCO→class mapping | **Done, frozen** — any change means re-running step 2 in both languages |
| `backend/` | Python 3.12 `uv` project, package `cane`: `cane.contracts` (Pydantic model + parser), `cane.transport` (NTP offset estimator, WebSocket event-link server, CLI) | **Done** (steps 2–3) |
| `ios/CaneKit/` | SwiftPM package: `CaneContracts` (Codable model + validation), `CaneTransport` (WebSocket client, pong responder, reconnect) | **Done** (steps 2–3) |
| `docs/steps/` | Per-step build briefs and architect design reviews — far more detailed than the runbook; each is the binding spec for that step | Steps 2, 3, 6, 7, 9, 10 (see its README) |

## Quick start

```bash
# Python (needs uv; CPython 3.12 is fetched automatically)
cd backend && uv sync && uv run pytest -q          # 101 tests

# Swift (Xcode 26 / Swift 6.3; tests run on the Mac, no device needed)
cd ios/CaneKit && swift test                       # 30 tests, includes a real phone↔laptop round trip

# Run the laptop-side event link and watch the clock offset (the phone app connects to ws://<laptop>:8765)
cd backend && uv run python -m cane.transport.server --port 8765 --demo-event-every 1
```

## Where each lane starts

- **P1 · perception (iOS)** — step 4 in `docs/orchestration-runbook.md`. Produce `AwarenessEvent(source: .phone)` and send it through `CaneTransport`. Bearings are torso-relative and must be normalized into `(-180, 180]`; the phone needs a frame-stable `track_id` (see `docs/steps/step-09-design.md` hazard H1). Add new Swift targets to `ios/CaneKit`, never a sibling package.
- **P2 · X4 bridge + cloud (Python)** — start from `docs/steps/step-06-brief.md` (grab thread, 6-crop dewarp, one-in-flight sender, and the bridge↔cloud inference API it defines for step 7), then `docs/steps/step-07-brief.md` for the L4 service. The prototype capture code it references lives in the separate `insta360` folder documented in `docs/insta360feed.md`.
- **P3 · experience (iOS)** — start from `docs/steps/step-09-brief.md` + `step-09-design.md` (feedback manager gauntlet, buildable today against `contracts/fixtures/` with no hardware), then `step-10-brief.md` + `step-10-design.md` (haptics, speech, audio session).

## Rules that keep three lanes from colliding

1. `urgency` is never on the wire; producers emit facts, the phone computes urgency.
2. The X4 path never states metres — `range_m` is null unless `source == "phone"`. All three validators enforce it.
3. All 11 keys are always present (nullable ones as explicit `null`); unknown keys are rejected.
4. The phone is the master clock; the cloud echoes `t_capture_ms` and never reads its own clock.
5. Commits: `<type>: <description>` (feat / fix / test / docs / chore). Tests first.
