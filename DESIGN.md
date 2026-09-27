---
name: Cane Live View
description: Airport wayfinding for a smart cane's live pipeline; every answer is a direction sign.
colors:
  ground: "#e2e4e3"
  ink: "#1a1a1a"
  ink-soft: "#4a4945"
  signal-yellow: "#ffd200"
  charcoal: "#2c2d2f"
  charcoal-text-soft: "#d5d3cd"
  charcoal-rule: "#4a4b4e"
  board: "#141414"
  board-raised: "#1f1f1f"
  board-line: "#2e2e2e"
  board-dim: "#a3a29d"
  white: "#ffffff"
  stop-red: "#d52b1e"
  stop-red-deep: "#a81f15"
  alarm-text: "#ff8479"
  alarm-line: "#ff5a4d"
  recorded-blue: "#0b5ea8"
  amber: "#ffbe00"
typography:
  display:
    fontFamily: "Avenir Next, Avenir, Helvetica Neue, Helvetica, Arial, sans-serif"
    fontSize: "64px"
    fontWeight: 700
    lineHeight: 1.02
    letterSpacing: "-0.025em"
    fontFeature: "tnum"
  headline:
    fontFamily: "Avenir Next, Avenir, Helvetica Neue, Helvetica, Arial, sans-serif"
    fontSize: "34px"
    fontWeight: 800
    lineHeight: 1.08
    letterSpacing: "-0.015em"
    fontFeature: "tnum"
  quote:
    fontFamily: "Avenir Next, Avenir, Helvetica Neue, Helvetica, Arial, sans-serif"
    fontSize: "28px"
    fontWeight: 600
    lineHeight: 1.2
    letterSpacing: "-0.015em"
  title:
    fontFamily: "Avenir Next, Avenir, Helvetica Neue, Helvetica, Arial, sans-serif"
    fontSize: "18px"
    fontWeight: 700
    lineHeight: 1.45
    letterSpacing: "-0.005em"
  body:
    fontFamily: "Avenir Next, Avenir, Helvetica Neue, Helvetica, Arial, sans-serif"
    fontSize: "15px"
    fontWeight: 500
    lineHeight: 1.45
    fontFeature: "tnum"
  value:
    fontFamily: "Avenir Next, Avenir, Helvetica Neue, Helvetica, Arial, sans-serif"
    fontSize: "16px"
    fontWeight: 700
    lineHeight: 1.45
    fontFeature: "tnum"
  label:
    fontFamily: "Avenir Next, Avenir, Helvetica Neue, Helvetica, Arial, sans-serif"
    fontSize: "13px"
    fontWeight: 700
    lineHeight: 1.45
    letterSpacing: "0.03em"
  mono:
    fontFamily: "ui-monospace, SF Mono, Menlo, monospace"
    fontSize: "13px"
    fontWeight: 400
    lineHeight: 1.5
rounded:
  pip: "2px"
  tag: "3px"
  plate: "4px"
  sign: "6px"
spacing:
  xs: "8px"
  sm: "12px"
  md: "18px"
  lg: "24px"
  section: "40px"
  gutter-mobile: "16px"
components:
  status-band:
    backgroundColor: "{colors.charcoal}"
    textColor: "{colors.white}"
    typography: "{typography.value}"
    height: "56px"
  status-band-brand:
    backgroundColor: "{colors.ink}"
    textColor: "{colors.white}"
    padding: "0 20px 0 16px"
  answer-sign:
    backgroundColor: "{colors.signal-yellow}"
    textColor: "{colors.ink}"
    typography: "{typography.display}"
    rounded: "{rounded.sign}"
    padding: "22px 28px"
    height: "196px"
  answer-sign-info:
    backgroundColor: "{colors.charcoal}"
    textColor: "{colors.white}"
    rounded: "{rounded.sign}"
  distance-inset:
    backgroundColor: "{colors.ink}"
    textColor: "{colors.signal-yellow}"
    rounded: "{rounded.plate}"
    padding: "14px 22px"
    width: "190px"
  said-strip:
    backgroundColor: "{colors.ink}"
    textColor: "{colors.white}"
    rounded: "{rounded.sign}"
    padding: "13px 18px"
  tag-heard:
    backgroundColor: "{colors.ink}"
    textColor: "{colors.signal-yellow}"
    typography: "{typography.label}"
    rounded: "{rounded.tag}"
    padding: "3px 8px 2px"
  tag-said:
    backgroundColor: "{colors.signal-yellow}"
    textColor: "{colors.ink}"
    typography: "{typography.label}"
    rounded: "{rounded.tag}"
    padding: "3px 8px 2px"
  plate:
    backgroundColor: "{colors.charcoal}"
    textColor: "{colors.white}"
    typography: "{typography.title}"
    rounded: "{rounded.plate}"
    padding: "7px 14px"
  plate-recorded:
    backgroundColor: "{colors.recorded-blue}"
    textColor: "{colors.white}"
    rounded: "{rounded.plate}"
    padding: "7px 12px"
  gate-chip:
    backgroundColor: "{colors.signal-yellow}"
    textColor: "{colors.ink}"
    typography: "{typography.label}"
    rounded: "{rounded.tag}"
    padding: "5px 8px 4px"
  flight-board:
    backgroundColor: "{colors.board}"
    textColor: "{colors.white}"
    rounded: "{rounded.sign}"
  flight-board-head:
    backgroundColor: "{colors.board}"
    textColor: "{colors.white}"
    typography: "{typography.title}"
    padding: "12px 16px"
  replay-sign:
    backgroundColor: "{colors.charcoal}"
    textColor: "{colors.white}"
    typography: "{typography.headline}"
    rounded: "{rounded.sign}"
    height: "150px"
  replay-sign-stop:
    backgroundColor: "{colors.stop-red}"
    textColor: "{colors.white}"
  live-stop-banner:
    backgroundColor: "{colors.stop-red}"
    textColor: "{colors.white}"
    padding: "14px 24px"
  button:
    backgroundColor: "{colors.ink}"
    textColor: "{colors.signal-yellow}"
    rounded: "{rounded.plate}"
    padding: "8px 16px"
  button-hover:
    backgroundColor: "{colors.signal-yellow}"
    textColor: "{colors.ink}"
  toggle-pressed:
    backgroundColor: "{colors.ink}"
    textColor: "{colors.signal-yellow}"
    rounded: "{rounded.plate}"
    padding: "3px 10px"
  stamp:
    backgroundColor: "{colors.board}"
    textColor: "{colors.white}"
    rounded: "{rounded.plate}"
    height: "92px"
---

# Design System: Cane Live View

## Overview

**Creative North Star: "The Concourse"**

The page is an airport concourse. Its ground is pale terrazzo, and on it stand the three kinds of sign a traveller already knows how to read: signal-yellow direction signs with black lettering and a drawn arrow, charcoal information bands with white text, and black flight-board panels that list what happened in time order. Cane's answer is always a direction sign (arrow, words, distance), so a judge standing at arm's length reads it the way they would read a gate sign: without being taught.

Density is mixed on purpose. The answer sign and the STOP moments are huge and sparse; the flight boards and diagnostics are dense and quiet. Colour is used as signage, not decoration: yellow means direction and "this one", red means STOP and nothing else, blue means RECORDED and nothing else. There are no cards, no drop shadows and no glow. Surfaces are flat plates with small sign radii, separated by colour and by the ground showing between them.

The one piece of theatre is borrowed from the departure hall: when a new answer lands, the sign's subject flips letter by letter like a split-flap display and settles left to right, and the recorded alarm's sign flips from charcoal to red at the frame the rule fires.

**Key Characteristics:**
- Pale concourse ground; every block of content is a flat, solid sign plate.
- Yellow direction sign with a drawn arrow is the hero, always.
- Black flight-board panels for anything that is a log or a list over time.
- Red is reserved for STOP; blue is reserved for RECORDED.
- One typeface (Avenir Next), heavy weights, tabular numerals everywhere.
- Split-flap letter settle on new answers; instant red flip on alarm.

## Colors

A wayfinding palette: one loud signal colour on a pale neutral ground, two dark sign materials, and two single-purpose safety colours.

### Primary
- **Signal Yellow** (`signal-yellow`): the direction sign's field, the HEARD and SAID tags, gate chips on camera views, the chosen-stamp border, the button's text and hover fill, the focus ring and the text-selection colour. It always carries black lettering, or appears as yellow lettering on black. Never yellow text on the pale ground.

### Secondary
- **Stop Red** (`stop-red`): the live STOP banner, the replay sign in its STOP state, stalled-feed flags, bad status pips. **Stop Red Deep** (`stop-red-deep`) is its text form on the pale ground (a "hot" readout value).
- **Alarm Salmon** (`alarm-text`) and **Alarm Line** (`alarm-line`): red as it must appear on black: alarm and error words in flight-board rows, the dashed alarm threshold on charts.

### Tertiary
- **Recorded Blue** (`recorded-blue`): the RECORDED plate beside the car-alarm heading and the RECORDED timestamp on the replay frame. Nothing else is ever blue.
- **Caution Amber** (`amber`): detections that are vehicles but not alarming (waiting, not coming, too far), in legends and diagnostics only.

### Neutral
- **Concourse Terrazzo** (`ground`): the page ground. It shows between every plate; it is the only "whitespace".
- **Sign Black** (`ink`): body text, the brand block, the SAID strip, the distance inset, buttons, tags, the ruled top border of readout rows.
- **Worn Black** (`ink-soft`): secondary text and notes on the ground.
- **Band Charcoal** (`charcoal`): the status band, section plates, the info-state sign and the replay sign at rest. **Charcoal Soft Text** (`charcoal-text-soft`) is its secondary text; **Charcoal Rule** (`charcoal-rule`) is the arrow-square divider on charcoal signs.
- **Flight-Board Black** (`board`), **Board Raised** (`board-raised`), **Board Line** (`board-line`), **Board Dim** (`board-dim`): the FIDS panel material, its newest/hovered row, its hairline row rules, and its secondary text.
- **White** (`white`): text on charcoal, black and red.

### Named Rules
**The Signage-Only Colour Rule.** Every chromatic colour is a sign meaning. Yellow is direction and selection, red is STOP, blue is RECORDED, amber is "vehicle, not alarming". If a colour does not answer "what does this sign mean", it does not belong.

**The One Red Rule.** Stop Red never decorates. It appears only when an alarm is live, recorded at its alarm frame, or a feed has stalled. A red element on a calm page is a bug.

**The Blue Is a Recording Rule.** Anything played back from disk wears Recorded Blue; anything live never does.

## Typography

**Display Font:** Avenir Next (with Avenir, Helvetica Neue, Helvetica, Arial)
**Label/Mono Font:** ui-monospace / SF Mono / Menlo, for raw JSON in diagnostics only

**Character:** One geometric-humanist sans in heavy weights (600 to 800), the lettering of transport signage. Numerals are tabular everywhere so clocks, metres and m/s never jitter as they update.

**Constraint:** Avenir Next is the system face installed on the demo MacBook, used by the user's choice; the user declined a web-font download and the page must work offline with no external resources. Keep the fallback stack; do not add @font-face or font links.

### Hierarchy
- **Display** (700, 64px, 1.02, -0.025em): the answer sign's subject ("Monster can"). Steps down to 46px when the subject runs past about 20 characters, and to 44px on phones. The distance figure in the inset is the same voice at 58px.
- **Headline** (800, 34px, 1.08): the replay sign's words ("Stop! Car coming ahead."); the live STOP banner uses the same weight at 38px.
- **Quote** (600, 28px, 1.2, balanced wrap): the heard question beside its HEARD tag. 22px on phones.
- **Title** (700, 18px): section plates and flight-board headings. Direction words on the sign sit at 22px/700; the SAID strip at 20px/600.
- **Body** (500, 15px, 1.45): running text, notes, the alarm-rule paragraph (max 80ch).
- **Value** (700, 16px): status-band values, station names and times, flight-board cells (600). Readout figures scale up to 26px/700 above a 13px/600 caption.
- **Label** (700, 13px, +0.03em, uppercase words): HEARD / SAID tags, flight-board column heads and row kinds, gate chips. Short words only, never sentences.

### Named Rules
**The Tabular Numbers Rule.** `font-variant-numeric: tabular-nums` is set on the body. Every number that updates live keeps its width.

**The Heavy Sign Rule.** Anything a judge must read from arm's length is 600 or heavier. 500 is for secondary prose only.

## Layout

Centred column, max 1680px, padded 22px top, 24px sides, 64px bottom; sections stack with a 40px gap and never sit inside a wrapping card. The status band is sticky at the top (56px); the live STOP banner sticks directly beneath it when an alarm is live.

The first section splits 64/36: left, the heard quote, the full-width answer sign, the SAID strip, then the picked frame beside the timed stations (a 300px column), with the row of image stamps under both; right, the black conversation flight board. The car-alarm replay splits 46/54: a square recorded frame, then the replay sign, a three-column readout row, the closing-speed chart and the transport controls; a full-width strip of all detector views runs under both.

Internal rhythm steps through 8, 12, 14, 18 and 24px. Label/value groups (readouts, diagnostic facts) are separated by a 3px black rule across their top, not by boxes.

Responsive: at 1180px and below every split collapses to one column. At 760px and below the gutter drops to 16px and section gap to 30px, the clock leaves the band and the status items become a three-column grid, answer and replay signs stack their arrow square above the words (arrow square 120px / 100px tall), readouts go to two columns, and the recent-questions board hides its last two columns.

## Elevation & Depth

Flat. Depth comes only from sign material: pale ground, then charcoal plates, then black boards, then yellow signs on top by contrast alone. There are no drop shadows anywhere. The only `box-shadow` in the system is the black halo that completes the focus ring.

### Shadow Vocabulary
- **Focus halo** (`outline: 3px solid signal-yellow; outline-offset: 2px; box-shadow: 0 0 0 5px ink`): every keyboard-focused control, so the yellow ring reads on both the pale ground and black boards.

### Named Rules
**The Flat Plate Rule.** Signs are painted plates, not floating cards. Separate them with ground, a rule or a change of material, never with a shadow or glow.

## Shapes

Small, consistent sign radii. Large signs and panels (answer sign, SAID strip, flight boards, camera feeds, replay view, chart) use 6px. Plates, insets, buttons, stamps and the recorded badge use 4px. Tags and gate chips use 3px. Status pips and station markers are 2px-rounded squares, never circles. Arrows are drawn strokes (15-unit stroke, mitred joins) rotated to the true bearing, set inside a square field divided from the words by a 4px rule. The stations list is a vertical line of 2px with square stops; skipped stations are hollow squares on a pale line.

## Components

### Status Band
Charcoal information band, sticky, 56px. A black brand block (yellow arrow mark, "Cane", "live view") on the left, a black clock block on the right (hours:minutes large, seconds small). Between them, status items divided by 1px rules: a small caption above a bold value with a 9px square pip. Pip is yellow when healthy, red (with salmon value text) when bad, grey when unknown; stale items drop to 60% opacity.

### Answer Sign (signature)
The hero. A yellow plate, 196px min height, three columns: a 196px arrow square (drawn arrow rotated to the bearing, 4px black divider), the words (direction line at 22px, subject in Display, a detail line), and a black distance inset showing metres in yellow with feet and "TrueDepth" in white. When no distance is measurable the inset shows a dim dash and the reason. Not-found and unavailable answers turn the sign charcoal with white lettering and drop the arrow. The idle sign invites "Hey Cane, ..." with example questions set as black-on-yellow-lettering chips. A new answer's subject settles with the split-flap flip (14 frames at 32ms).

### HEARD and SAID
The heard question is a Quote-size line after a black HEARD tag with yellow lettering, with its time and a "Follow newest" toggle at the end. Under the sign, the SAID strip is a black bar with a yellow SAID tag and Cane's spoken words in white 20px/600; before the phone speaks it says so in dim text.

### Stations
A timed route: Heard, Looked, Gemini answered, TrueDepth measured, Cane spoke. Square stop, bold station name, right-aligned `+0.7 s` time, a secondary detail line. Stations that did not happen are hollow and their connecting line pales.

### Stamps
A horizontal row of every image sent to Gemini, 92px tall thumbnails on black with a caption bar. The picked image gets a yellow 3px border, black outline and a yellow caption beginning "PICKED"; the image currently shown large gets a white border.

### Flight Boards
Black panels for time-ordered logs: conversation, recent questions, phone feed, alarms, hazard tables. A heading row (18px/700 white, dim note right-aligned) over rows split by hairline `board-line` rules. Row kinds are uppercase labels coloured by meaning: HEARD yellow, SAID white, ALARM/error salmon, the rest dim. The newest row sits on `board-raised`; hovered rows too; a selected row gets a dark yellow-tinted fill. Result words in recent questions are uppercase labels: FOUND yellow, NOT FOUND dim, DESCRIBED white, UNAVAILABLE salmon.

### Plates
Section headings are charcoal plates (18px/700 white, 4px radius, 7px 14px padding) with a plain-text note beside them. A plate turns red only for a STOP section. The diagnostics disclosure uses the same plate with a rotating chevron.

### Gate Chips
Camera images carry no text, so each view is named by a yellow gate chip with black lettering pinned to its top-left ("C0 · ahead", "120 L"). A larger chip names the focused view in the replay.

### RECORDED Plate and Replay Sign (signature)
Anything from disk is marked by a blue RECORDED plate (with its date) beside the "Car alarm" plate, and a blue RECORDED timestamp on the replay frame. The replay sign uses the answer sign's grammar at 150px: arrow square, Headline words, a black inset showing closing speed. At rest it is charcoal and says "No alarm. Cane stays quiet."; at an alarming frame it flips to Stop Red (a 0.12s linear change), the divider turns black and the speed turns yellow, and it holds red 1.5s after the last alarming frame. Chart alarm spans stay grey until the playhead reaches them, then turn red.

### Live STOP Banner
When a real alarm is live, a full-width red banner sticks under the status band: the spoken warning at 38px/800, the reason (closing speed, range band) beside it, and a white-bordered LIVE tag at the end.

### Buttons
- **Shape:** gently squared (4px).
- **Primary:** black with yellow lettering, 15px/700, 8px 16px, an optional 14px drawn glyph.
- **Hover:** inverts to yellow with black lettering.
- **Disabled:** pale warm grey fill with off-white text, default cursor.
- **Toggle ("Follow newest"):** transparent with a 2px black border; pressed state is black with yellow lettering.
- **Select (recorded run picker):** white, 2px black border, 4px radius, 600 weight.

### Readouts
Three label/value columns under a 3px black rule: 13px/600 caption in worn black above a 26px/700 value. A value that crosses the alarm threshold turns Stop Red Deep.

## Do's and Don'ts

### Do:
- **Do** show every answer as a direction sign: drawn arrow at the true bearing, words, and a distance inset.
- **Do** keep signal yellow paired with black: black lettering on yellow, or yellow lettering on black.
- **Do** put anything time-ordered on a black flight board with hairline row rules.
- **Do** mark every played-back recording with the blue RECORDED plate, and keep live views free of blue.
- **Do** set every live number in tabular figures at 600 weight or heavier.
- **Do** honour reduced motion: the split-flap flip and all transitions switch off under `prefers-reduced-motion`.
- **Do** keep the page self-contained: Avenir Next from the system with its fallback stack, no web fonts, no external resources.

### Don't:
- **Don't** use Stop Red for anything but STOP, alarms and stalled feeds.
- **Don't** add drop shadows, glows or floating cards; separate plates with ground, rules or material.
- **Don't** build a dark tile-grid ops dashboard; the ground is pale and the answer sign leads.
- **Don't** round beyond the sign radii (6px large, 4px plates, 3px tags) or use circles for pips and station stops.
- **Don't** place yellow text on the pale ground or on white; it has no contrast there.
- **Don't** stage anything as live that was recorded.
