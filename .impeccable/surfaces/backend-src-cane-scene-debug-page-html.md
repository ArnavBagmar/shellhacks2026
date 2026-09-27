---
version: 1
slug: "backend-src-cane-scene-debug-page-html"
primary_target: "backend/src/cane/scene/debug_page.html"
related_targets: []
---

# Surface: Cane live view (debug page, localhost:8780)

Mode: Operate (a live instrument that judges watch and the team debugs from).

Audience: ShellHacks judges at the MacBook's own screen, standing or sitting close; the team during the demo.
Task: understand at a glance what Cane just heard, saw, decided, measured and said; see the 360 camera and object detection live; watch a recorded car-alarm run (the alarm can't be triggered live).
Constraints: one self-contained HTML file, no network; font is Avenir Next (installed on this Mac, user's choice); keep `/state.json` contract; diagnostics stay available for the team.

## Direction contract

THESIS: Every answer Cane gives is a direction sign, so the page shows it as one: arrow, words, distance. Refuses the dark tile-grid ops dashboard.

OWN-WORLD: Airport wayfinding. Pale concourse ground; signal-yellow direction signs with black lettering and a drawn arrow; charcoal information bands with white text; black flight-board (FIDS) panels for logs; safety red only for STOP; information blue only for the RECORDED plate. Avenir Next, bold, tabular numerals. Small sign radii, no cards, no shadows, no glow.

STORY: Judge reads the heard question, the yellow sign with arrow and TrueDepth metres, the frame Gemini picked with its box, the timed stations, what Cane said; sees live 360 plus detections; plays the RECORDED alarm run where the sign flips to red STOP as closing speed crosses 1.5 m/s.

FIRST VIEWPORT: Charcoal status band with clock. Left 64%: heard quote, full-width yellow answer sign (arrow square, subject 60px+, direction words, black distance inset), Cane said strip, then picked frame beside timed stations. Right 36%: black flight-board conversation log. No primary action; follow-newest toggle only.

FORM: Airport Wayfinding, my top-ranked grounded candidate (1 of 7), taken as IMPECCABLE'S PICK over roll 5 (Tactile Paving); seed key e16e5bd7. Signature interaction: split-flap letter flip on the sign when a new answer lands, and the replay sign flipping to red STOP at the alarm frame.

FINISH: unreviewed and undocumented is unfinished; this build ends with the finish review, the verdict, DESIGN.md, and every shipping raster carrying its provenance

## Open
- Replay needs one real recorded alarm clip from `./startcane full` near a street.
