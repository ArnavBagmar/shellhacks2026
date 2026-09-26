# Fused preview — objects and relative depth in one view

`cane.benchmarks.fused` runs **YOLOE detection and Depth Anything V2 Small over
the same captured frame** and draws both into a single image, instead of the two
separate windows produced by `cane.benchmarks.yolo` and `cane.benchmarks.depth`.
Either layer can be switched off from the window or at startup.

Still no metric distance, navigation warning, safety threshold, tracking, event
transport or cloud call. This is a local visual-inspection and timing tool.

The two standalone runners stay: they are the per-model baselines that
[`yolo-benchmark.md`](yolo-benchmark.md) and [`depth-benchmark.md`](depth-benchmark.md)
document, and a fused number cannot be compared against a single-model one.

## Setup (from `backend/`)

Both model setups are required — the vision extra for YOLOE, and the depth extra
plus the pinned official source checkout and checkpoint:

```bash
uv sync --extra vision --extra depth
mkdir -p third_party checkpoints
git clone https://github.com/DepthAnything/Depth-Anything-V2.git third_party/Depth-Anything-V2
git -C third_party/Depth-Anything-V2 checkout a561b849ebae10a6f5ef49e26c83cbbcd36c71bf
curl -L --fail --retry 2 https://huggingface.co/depth-anything/Depth-Anything-V2-Small/resolve/main/depth_anything_v2_vits.pth -o checkpoints/depth_anything_v2_vits.pth
```

Skip whatever is already in place. `--no-depth` skips the depth requirements and
`--no-detection` skips the YOLOE ones, so a partial setup still runs one layer.

## Run (from `backend/`)

```bash
PYTHONPATH=src .venv/bin/python -m cane.benchmarks.fused --camera 0 --show --max-frames 0
```

Recorded input, which is what model comparisons should use:

```bash
PYTHONPATH=src .venv/bin/python -m cane.benchmarks.fused --source /absolute/path/route.mp4 --show
```

`--list-cameras` lives on the YOLOE runner and probes indices without loading a
model. CUDA → MPS → CPU is selected automatically; `--device` forces one.

**Depth runs at `--input-size 196` here, not the 280 the standalone depth
benchmark uses.** Depth is over half the frame budget in the fused view and 196
nearly halves it (52 ms → 28 ms measured on an M2 at 1280×720), at the cost of
fine depth detail: thin poles and railings blur first, and per-box `rel` values
get noisier for small or distant objects. The standalone runner keeps 280 so its
recorded baseline stays comparable with every run already taken. Pass
`--input-size 280` to match it, or `518` for upstream's default.

## Controls

The toolbar states each layer's name, whether it is **ON** or **off**, and its
key. Click a button or press its key — they do the same thing.

| Button / key | Effect |
|---|---|
| `Depth` · `d` | Depth inference **and** its overlay. Off costs no time at all. |
| `Objects` · `o` | YOLOE inference, boxes and labels. Off costs no time at all. |
| `Masks` · `m` | Instance-mask outlines. Drawing only; masks are computed by the same checkpoint either way. |
| `Corridor` · `c` | Central-third guide lines and the centre depth probe. Visual reference only. |
| `Quit` · `q` / `Q` / `Esc` | Stop and write the summary. The window close button does the same. |
| `s` | Save the frame on screen with its raw depth and detections. |
| **Depth %** slider | Overlay strength. Moving it also switches the depth layer back on. |

A greyed button reading `not loaded` means that model was disabled at startup
with `--no-depth` / `--no-detection`; it cannot be switched on mid-run, because
its weights were never loaded. Click the preview to focus it before using keys.

## Reading the picture

**Colour means relative depth, never metres.** Within each frame:

```
relative = (raw - raw.min()) / (raw.max() - raw.min())
```

**Light green is farther, dark orange is nearer**, on a ramp that gets darker and
warmer as it approaches the near end. The heatmap, the box outlines and the label
plates all read from the same 256-entry table, so a box's colour is the colour of
the depth underneath it. Guides and chrome are cool blue precisely so nothing but
depth is ever green or orange.

Each label reads `class confidence | rel 0.72`. That `rel` is the **median
normalized depth over the central 25% of the box area** (a quarter trimmed off
each side), which keeps background pixels visible around an object inside a
rectangular box from dragging the value around.

Normalization is per frame, so values are not comparable across frames, are not
calibrated, and moving an object closer need not raise its number monotonically —
especially when it was already the nearest thing in view. A flat depth map is
flagged `FLAT` rather than shown as a distance. Colour is not evidence of accuracy.

When two labels would overlap, the **nearer** object keeps its text and the
farther one shows its box without a label. Nothing is hidden from the recorded
data: `frames.jsonl` still carries every detection.

## Live capture: newest frame only

On a **camera**, a background thread reads continuously and keeps exactly one
frame. The loop always processes the freshest one; everything the loop was too
slow for is dropped and counted in `frames_dropped`. Without this the driver's
own buffering serves a frame from 100-300 ms ago and nothing says so -- the
failure `camera-sync-design.md` §3.3 rule 1 exists to prevent, and which the X4
bridge's `capture/grab_thread.py` already solves on its side.

Measured on an iPhone Continuity Camera: read time falls from ~24 ms to ~0,
because a frame is already waiting rather than being waited for.

**Cameras only.** Files, directories and video keep `Frames`' no-skipping
guarantee -- every frame processed, in order -- which is what makes two
benchmark runs comparable frame for frame. `--no-latest-frame` restores the old
inline behaviour on a camera too: slower and laggier, but nothing is dropped.

Two consequences, both recorded in `summary.json`:

- `frame_id` counts frames **processed**, not frames the camera produced.
  `identity` carries the camera's grab sequence, so the gap is visible.
- `read_ms` and `frame_ms` from a latest-frame run are **not comparable** with
  runs made without it, nor with the file path. `latest_frame_grab` says which
  mode produced the numbers.

## Same-frame guarantee

One frame per iteration. Both models receive the identical unannotated
frame, sequentially, and drawing happens afterwards on a copy. There is no
interleaving, frame skipping, or reuse of a previous frame's depth, so a box and
the depth under it always describe the same pixels. `frames.jsonl` records one
`bgr_sha256` per frame covering both layers, which is what makes that checkable.

`docs/build-plan.md` §1.4 requires interleaving depth at 5–10 Hz **on the phone**.
That trades the guarantee above for latency and belongs in the iOS pipeline, not
in this inspection tool — pairing a new frame with an older depth map here would
quietly invalidate every `rel` value on screen.

## Artifacts and timings

`benchmark_runs/fused-*/` holds `summary.json`, `timings.csv`, `frames.jsonl`,
and, for saved samples, `frame_NNNNNN.jpg` with the **raw, unnormalized**
`depth_NNNNNN.npy` and `detections_NNNNNN.json`.

- `depth_predict_ms`, `depth_preprocess_ms`, `depth_inference_ms`,
  `depth_postprocess_ms`: the depth stages, as in the standalone depth runner.
- `detect_ms` plus `detect_preprocess_ms` / `detect_inference_ms` /
  `detect_postprocess_ms`: the YOLOE stages, as in the standalone YOLOE runner.
- `predict_ms`: depth + detection for the frame.
- `result_ms`: normalization, centre probe and per-box medians.
- `frame_ms`: read + predict + result. Drawing, saving, hashing and display are
  excluded, so its reciprocal is **not** display FPS or capture-to-feedback latency.

**A layer switched off contributes 0 ms to its own stages.** `layer_frames` in
`summary.json` counts how many frames ran each layer; aggregates are only
comparable across frames where the same layers ran. Toggling mid-run is a preview
convenience — for a timing run, fix the layers with the CLI flags and leave them.

Stage barriers add profiling overhead. Warmup and model loading are excluded, and
initial camera startup is reported separately as `camera_startup_ms`.

## Checks executed during implementation

- Python tests pass, including the merged-view, palette and latest-frame tests.
- Real YOLOE-26 Small + Depth Anything V2 Small on Apple MPS over the official
  `demo01.jpg`: 19 detections, all four layer combinations, and the both-layers-off
  flag combination rejected with an explicit message.
- Depth direction held on that frame: the foreground car read `rel 0.93` and the
  distant bus `rel 0.28`, matching the visibly nearer region. One qualitative
  direction check, not a distance calibration.
- Palette endpoints asserted in tests: value 0 is light and green-dominant, value
  1 is dark and red-dominant, and luminance falls monotonically between them.
- Single-image timing on MPS, 1 warmup: depth ≈ 60 ms, detection ≈ 48 ms,
  frame ≈ 122 ms. **One warmed-up sample of one still image** — not sustained
  live FPS, and the standalone runners' numbers remain the per-model baselines.
- **Not verified:** the live camera window, its buttons and slider under a real
  display, Continuity Camera, sustained throughput, and CUDA.
