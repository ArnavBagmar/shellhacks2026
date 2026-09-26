# docs/steps — per-step briefs and design reviews

Each `step-NN-brief.md` is the binding, self-contained requirements for that runbook step: the verbatim spec sections it depends on, plus the controller rulings that resolve every ambiguity. Each `step-NN-design.md` is the architect review of that brief (verdict + amendments); where a design amends the brief, **the amendment wins**.

| Step | Brief | Design | State |
|---|---|---|---|
| 2 · Event contract + golden fixtures | step-02-brief.md | step-02-design.md | built, reviewed |
| 3 · NTP handshake + WebSocket transport | step-03-brief.md | — | built, reviewed |
| 6 · X4 bridge (grab, dewarp, sender) + bridge↔cloud API | step-06-brief.md | — (api_models amended by step-07-design A2) | built, live-tested on the X4, reviewed, fixed. Live artefacts in `assets/` |
| 7 · Cloud inference service (TensorRT on L4 / YOLO11s on MPS locally) | step-07-brief.md | step-07-design.md (15 binding amendments) | built, reviewed, fixed, live-tested on MPS (real detections in `assets/`); L4 tier UNVERIFIED until the GPU quota lands |
| 8 · Health monitor, rung stepping, local fallback | step-08-brief.md | — | built, reviewed, fixed; flip verified live |
| 9 · Feedback manager gauntlet | step-09-brief.md | step-09-design.md | built, reviewed, fixed |
| 10 · Haptics + TTS + panning | step-10-brief.md | step-10-design.md | ready to build (P3) |
| 12 · Navigation: Routes API + turn cues | step-12-brief.md (incl. verified API reference §H) | — | built, reviewed, fixed; live check needs a Maps key |

Steps not listed here have only their runbook entry so far (`docs/orchestration-runbook.md`).
