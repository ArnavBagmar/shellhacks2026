# Step 26 brief — the voice layer sees the gap profile, and `blocked` gets a voice

Written after implementation, 2026-09-26. Binding spec: `docs/shepherd-steering-plan.md`
§0, §2.5, §3.1 ruling 2, **§3.2 ruling 4**, §3.3 ruling 3, §4 and its Step 26.
`contracts/transport_envelope.schema.json` was **not** touched — the `answer` frame is
frozen and this step works inside it. Nothing was committed.

## A. What was built

```
cloud /infer ─gap_profiles─> bridge pipeline ─steer_from_response─> Steer ──> phone (cue)
                                                                      │
                                                          SteerTap ───┘  (--scene only)
                                                              │
                                                     LatestClearWay (2 s expiry)
                                                              │
                              SceneQueryService ──────────────┘
                                  │                    │
                        prompt: GapHint            answer: ClearWay
                     (image index + x)        (sector words in `detail`)
```

### New files

| file | what it is |
|---|---|
| `backend/src/cane/scene/gap.py` | `ClearWay` (one ring reading), `ClearWayProvider`, `LatestClearWay` (single slot + expiry), `SteerTap` (the pass-through sink that feeds it) |
| `backend/src/cane/scene/gap_speech.py` | a bearing → the phone's own sector words → the two wearer-facing clause forms; the module that is provably free of metres |
| `backend/tests/scene/test_gap.py` | the reading, the store's expiry, the tap, the words, the no-distance sweep, the bridge wiring decision |
| `backend/tests/scene/test_gap_prompt.py` | prompt geometry and growth, and fixture rings driven through the real `steer_from_response` into real answers |

### Changed files

| file | change |
|---|---|
| `backend/src/cane/scene/prompt.py` | `GapHint`, `gap_clause`, `build_prompt(query, gap=None)`. With `gap=None` the prompt is **byte-identical** to step 20's, structurally: the clause is a suffix |
| `backend/src/cane/scene/service.py` | `clear_ways=` provider; `View.x_for_bearing` (the inverse of `bearing_at`); `gap_hint_for`; the ring reading is read **once** and drives both the prompt and the answer; `_dump` records the prompt actually sent |
| `backend/src/cane/bridge/__main__.py` | `build_steer_sink(server, clear_ways)` — the wiring point. `--scene` creates a `LatestClearWay`, hands it to `SceneQueryService`, and routes the steer stream through `SteerTap` |

Nothing under `backend/src/cane/bridge/{gap_ring,steering,steer_frame,gap_anchor,config}.py`,
`backend/tests/bridge/`, `ios/`, `contracts/`, `cloud/` was edited.

## B. Decisions (binding)

| # | Decision | Why |
|---|---|---|
| B1 | The voice layer consumes the **`steer` frame the phone was sent**, not the 48 columns | removes the second opinion rather than testing for it: the words and the buzz are rendered from one number, so "the left is clear" while the cue steers right is structurally impossible |
| B2 | The ring reaches Gemini as **an image index and a horizontal position**, never as a side word | `prompt.py`'s principle, made symmetric. `View.bearing_at` is run backwards, so the model is pointed at the pixels our own geometry chose and cannot echo a direction we did not compute |
| B3 | The side the **wearer** hears is rendered by `service.py`, in `CaneOutput/BearingSector.swift`'s vocabulary | the frozen `answer` contract has one `bearing_deg` and only a `found` answer may carry it, so a description has no other channel. The words still come from geometry, never from the model |
| B4 | Wording is **comparative, never metric** — "most open to your right" | §2.5: `anchored` is false in the field, and §3.3 ruling 3's rule is scale-*invariant*, so it knows which way is freer and not metres. A sweep of every bearing × `blocked` × `anchored` asserts no digit and no unit is ever produced |
| B5 | `anchored` is carried but changes **nothing** today | a guard, not a feature: step 27 turning metres on has to come through `gap_speech.py` and break `test_the_words_do_not_change_when_the_anchor_is_accepted` on purpose |
| B6 | A reading **expires** after `5 × STEER_TTL_MS` = 2000 ms | the bridge sends no `steer` in local-fallback mode (§4.2), so the voice layer must fall silent about the ring rather than quote the last one. 2 s survives the 400 ms settle plus a 3 Hz tick and dies inside one spoken answer. The cue's own 400 ms is for a *continuous* signal driving an actuator and is far too tight for prose |
| B7 | A `blocked` ring with its least-bad column **dead ahead claims no side** | §3.3 ruling 3's structureless ring lands exactly there, and "no clear path ahead, most open straight ahead" is a contradiction spoken aloud |
| B8 | On a `found` answer the clause rides along **only when `blocked`** | a found answer already carries one direction; a second unrelated one in the same breath is the confusion this step exists to stop, wearing a different hat. `blocked` is the exception because §3.2 ruling 4 leaves it nowhere else to go |
| B9 | A ring **never** rescues an `unavailable` answer | a depth ring is not a description. Returning `described` because the model failed would tell the wearer their question was answered when it was not |
| B10 | `not_found` / `unavailable` get no clause | `AnswerPhrasing` discards `detail` for both, so it would be dead weight the wearer never hears |

## C. The exact prompt growth

Appended after the step-20 prompt (trailing, because "trust this over your own guess" is an
instruction and instructions are followed best last). Four pieces, one sentence:

```
A depth sensor saw the same scene: the clearest way through is in image {i} at horizontal
position {x:.2f} (0 left edge, 1 right edge){, and there is no clear path ahead}. Trust
that over your own guess about which way is clear, and state no distances.
```

and, when the gap falls outside every image sent (a `describe_front` with a phone photo
sends only its ~70° view, while the ring spans ±90°):

```
A depth sensor saw the same scene: the clearest way through is outside these images{, and
there is no clear path ahead}. Trust that over your own guess about which way is clear,
and state no distances.
```

| variant | chars |
|---|---|
| at an image, not blocked | 212 |
| at an image, blocked | **246** (the worst case) |
| off image, blocked | 200 |
| off image, not blocked | 166 |
| `MAX_GAP_CLAUSE_CHARS` (asserted) | 256 |

Prompt totals: `describe_front` 433 → 679, `describe_around` 427 → 673, `find` 824 → 1070.
**+246 characters ≈ 62 tokens, on every kind.** Against six 512 px q75 JPEGs at medium
media resolution the request is dominated by image tokens by more than an order of
magnitude, so this is inside step 20's measured budget (flash-lite 1.1–2.6 s, hedged, 15 s
cap). `MAX_GAP_CLAUSE_NUMBERS = 4` is asserted too: the image index, the x, and the two
digits of the edge convention. Never 48 floats.

## D. How 48 columns were summarised, and why it cannot mislead

They were not summarised here **at all** — and that is the point. `steering.py` has already
reduced the ring to two values the whole system agrees on:

* `gap_bearing_deg` — the chosen run's centroid, or the least-bad column when `blocked`
  (§3.1 ruling 2);
* `blocked` — "do not trust this as a path".

Those two *are* the summary, they are the ones `steer` carries to the phone, and they are
what the haptic cue is rendered from. Any per-sector average computed inside `cane.scene`
would be a **second judgement** over the same 48 numbers and could disagree with the cue —
which is the exact failure this step exists to prevent. A model does not need 48 numbers to
say "the right is more open"; it needs the one bearing that was already chosen, and it gets
it as pixels.

Two honesty properties come free from consuming the frame rather than the columns:
`steer_frame.py` already refuses to emit one for a local response, for a ring with no
evidence, or for a bearing outside the forward arc — so **a reading here is always a real
measurement**, and a bridge that stopped sending makes the voice layer go quiet (B6).

## E. The exact wording a wearer hears

`detail` is what `AnswerPhrasing` speaks: verbatim for `described`, inlined after a comma
for `found`. So there are two renderings of one fragment.

| situation | ring | spoken (`describe_front`) | inlined (`find`) |
|---|---|---|---|
| clear right side (wall on the left, gap +45°) | not blocked | `"Most open ahead to the right. a corridor and a bench"` | — (B8) |
| mirrored (wall on the right, gap −45°) | not blocked | `"Most open ahead to the left. …"` | — |
| gap dead ahead | not blocked | `"Most open straight ahead. …"` | — |
| **blocked path**, least-bad +88.125° | blocked | `"No clear path ahead, most open to your right. a wall and a bin"` | `"no clear path ahead, most open to your right; past the elevators"` |
| **blocked**, structureless ring (+1.875°) | blocked | `"No clear path ahead."` | `"no clear path ahead"` |
| **unanchored ring** (every live frame, §2.5) | either | exactly the rows above — comparative, no metres, no digits | same |
| no ring, or a ring older than 2 s | — | unchanged from step 20 | unchanged |

End to end through the phone's own phrasing, a blocked `find` becomes:

> "To your right, restroom sign, no clear path ahead, most open to your right."

and a blocked `describe_front`:

> "No clear path ahead, most open to your right. A wall and a bin."

`anchored` never changes a word (B5), so criterion "no metres while unanchored" holds by
construction rather than by luck.

## F. What was wrong, ambiguous, or deviated from in the plan

1. **Step 26's "let `.navigate` cues mention the passable side" cannot be done from the
   backend, and the same brief forbids the only way to do it.** `.navigate` is an iOS
   intent: `IntentParser` → `VoiceRouter` → `NavigationEngine` (`ios/CaneKit`), entirely on
   the phone. There is no `navigate` in `QueryKind` and no wire shape that carries a turn
   cue to the laptop, so naming the side inside a turn cue needs an `ios/` change plus a
   contract amendment — both explicitly out of scope ("no new voice intent", "do not touch
   `ios/**`/`contracts/**`"). **Deviation:** the substance was delivered where it is
   reachable — a `blocked` ring names the passable side on the answer that *is* the
   backend's own turn cue, the `found` answer ("To your right, restroom sign, no clear path
   ahead, most open to your right"). Making a real `.navigate` cue say it is a phone-side
   follow-up and should be filed as such.
2. **"Which way is clear" is not a parseable utterance today.** `IntentParser.parse("which
   way is clear")` returns `nil` (no find prefix, not in `frontPhrases`/`aroundPhrases`), so
   the wearer gets "Sorry, I didn't catch that". The question is answered by
   `describe_front` ("what's in front of me", "what do you see"), which is what this step
   wired. Adding the phrase is a one-line `frontPhrases` change in `ios/` — out of scope
   here, worth doing before the demo.
3. **§2.5 is live and nothing in this step pretends otherwise.** Every fixture ring in the
   suite runs with `anchored=false`, which is the field condition, and the anchored branch
   of the vocabulary does not exist rather than existing untested.
4. **Criterion 5 has a boundary worth stating out loud.** No wording *we generate* can state
   a distance — swept and asserted. The model's own prose is *instructed* not to ("state no
   distances") but not filtered: scrubbing metre-like substrings out of a description would
   mangle legitimate text, and a VLM's "a bench a few metres away" is a visual estimate, not
   our depth ring claiming a scale it does not have. The line drawn is: **we never generate
   a metre, and we never let the model's guess about which way is clear override the ring.**
5. **The `answer` contract's single `bearing_deg`, reserved for `found`, is what forces the
   clear side into words.** Not a defect — but it is why B3 exists, and it is the reason the
   "direction stays geometric" rule is honoured in *origin* (geometry decides) rather than
   in *representation* (a second bearing field). If a future amendment adds one, the phone
   should speak it with `BearingSector` and this module's strings should shrink to the
   `blocked` message alone.
6. **The plan calls this step "one prompt change, one plumbed argument".** It is two plumbed
   arguments (the provider into the service, the tap into the steer sink) and one new
   wiring helper, because the ring lives in the pipeline and the prompt lives in the query
   task. No pipeline or steering file changed.

## G. Tests

```bash
cd backend && PYTHONPATH=src uv run pytest tests/scene -q          # 90 passed, 1 skipped
cd backend && PYTHONPATH=src uv run pytest tests --ignore=tests/bridge -q   # 802 passed, 1 skipped
```

The skip is the pre-existing `tests/scene/test_gemini_fallback.py` (`importorskip`
`google.genai`, the optional `scene` extra). `tests/scene/test_service.py` was **not**
edited; step 20's 28 tests pass as written. No real Gemini call was made — `GEMINI_API_KEY`
was not in the environment and `FakeVLM` is what the suite uses.

The RED seen first: `ModuleNotFoundError: No module named 'cane.scene.gap'` collecting both
new test modules, then `ImportError: cannot import name 'build_steer_sink' from
'cane.bridge.__main__'` for the wiring test.

Coverage of the new code (pytest-cov, `--cov=cane.scene`): `gap.py` 100%, `gap_speech.py`
100%, `prompt.py` 100%, `service.py` 98% (the two misses are pre-existing: the
`CancelledError` re-raise and `_dump`'s `OSError` branch).

Also smoke-run so the wiring point is known to still start:
`uv run python -m cane.bridge --source synthetic --local-detector fake --allow-unsynced
--duration-s 3` — bridge up, flip to local, clean shutdown, `steer` frames correctly absent
(local mode sends none).

## H. Not done / follow-ups

* A real `.navigate` turn cue naming the passable side (F1) — phone side.
* "Which way is clear" as an utterance (F2) — one line in `IntentParser.frontPhrases`.
* Live Gemini latency with the clause attached — needs `GEMINI_API_KEY` and belongs with
  step 25's walk. The character/token cost is measured above; the network cost is unchanged.
* `cane.scene.__main__` (`--serve`, `--equirect`) has no ring and does not pretend to; a
  `--fake-ring` flag would make the wording demoable without the X4.
