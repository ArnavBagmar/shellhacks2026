# Product

<!-- impeccable:product-schema 1 -->

## Platform

web

Design scope is the laptop debug page only (`backend/src/cane/scene/debug_page.html`, served at `http://localhost:8780`). The iPhone app (`ios/CaneApp`) is out of scope for design work: nothing about the phone changes.

## Users

- **ShellHacks 2026 judges** watching the debug page live on the laptop while the team demos the smart cane. They need to see, at a glance and in real time, what the system perceives and decides.
- **The team** (Kavi, Arnav, Stanley) running and debugging the demo from the same page: camera feeds, Gemini picks, hazard verdicts and stats.

The cane's actual wearer is a blind or low-vision walker who never looks at a screen. They are not an audience for this page, but the page is showing a tool built for them.

## Product Purpose

The smart cane is an assistive extension to the white cane. A chest-mounted iPhone listens for "Hey Cane" and answers spoken questions ("is there a bathroom around?", "how far is the Sprite can?") using Gemini over the phone's front camera and a head-worn Insta360 X4 360 view. It also sounds "Stop! Car coming from your left." when a car or bus is closing on the wearer.

The debug page makes this invisible pipeline visible: live 360 feed and dewarped crops, which image and box Gemini picked, what the phone heard, decided and said, and the hazard board (radar, closing-speed chart, per-detection verdicts, alarm episodes).

Success this weekend: **win the ShellHacks demo.** The page is the live view the judges watch.

## Positioning

The page shows real perception as it happens, not a mock: the actual frames Gemini saw with its box drawn on the right image, the TrueDepth distance, and every car detection with the reason it did or did not alarm. That end-to-end explainability of the phone + 360 camera + laptop pipeline is the thing to show.

## Operating Context

- Started by `./startcane` (phone camera only), `./startcane x4` or `./startcane full` (X4 + detection + hazards). It prints the page address.
- Served by `debug_view.py` (`DebugHub`, stdlib HTTP server bound to 127.0.0.1 only). The page polls `/state.json` and shows MJPEG streams `/live.mjpg` and `/hazards.mjpg`.
- The hazards section appears only when detection runs (`startcane full`).
- Runs on the team's MacBook during a live hackathon demo, on venue Wi-Fi or with no internet at all.
- Open: whether judges see the laptop screen directly or on a projector or larger display, and at what resolution.

## Capabilities and Constraints

- One self-contained HTML file with inline CSS and JS and no external resources. Keep it that way so it works offline and loads instantly.
- The JS reads element ids and `/state.json` field names. Changing the layout must keep the data contract with `debug_view.py` and `hazard_view.py`.
- Tests assert on page content (for example the heading "Hazards: cars coming at you" in `backend/tests/scene/test_hazard_view.py`). Update the tests with any copy change.
- Terms: "360 camera" / X4, crops C0–C5 plus floor crop C6, "phone image", Gemini `box_2d`, closing speed (m/s), range bands near (≤ 5 m), mid (5–15 m), far. Alarm rule: a car or bus in near or mid, closing at ≥ 1.5 m/s.
- The page must reflect what the phone actually does. The hazard rules are sync-tested against the Swift `HazardScope`.

## Brand Commitments

- Product name "Cane"; wake phrase "Hey Cane".
- The spoken voice is short, calm and direction-first ("Stop! Car coming from your left.", "Hmm, let me look around.").

## Evidence on Hand

- Live data only: camera frames, Gemini replies, phone diagnostics, hazard tracks. No sample or fabricated data should appear as if it were live.
- Still image for demos without the X4: `docs/steps/assets/step-06-live-equirect.jpg`.
- Saved views from past runs: `logs/views` (gitignored).
- There are no users, testimonials or benchmark claims to cite. The measured numbers in `docs/` (for example `yolo-benchmark.md` and `depth-benchmark.md`) are the only performance evidence.

## Product Principles

1. **Show the real pipeline, live.** Every number and box comes from the running system. Empty or disconnected states say so plainly instead of faking activity.
2. **Legible from a judge's distance.** A judge who glances at the page should understand what Cane just saw, decided and said.
3. **Safety moments dominate.** A car alarm is the most important event on the page and must be unmistakable when it fires.
4. **Debuggable first.** The team still has to diagnose a failing demo from this page. Clarity for judges must not remove the diagnostics.
5. **Never break the demo.** Offline, dependency-free and fast. The page should degrade gracefully when a feed stalls.
