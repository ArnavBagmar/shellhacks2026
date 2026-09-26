# Standalone YOLOE benchmark

First component benchmark, independent of phone transport and cloud. No depth,
scene segmentation, tracking, distance, or warnings yet. For detections and depth
drawn together on one frame, see [`fused-preview.md`](fused-preview.md); this
runner stays as the detection-only baseline. Default: **YOLOE-26 Small**,
not COCO-only YOLO. This checkpoint computes boxes **and instance masks**; timing
includes both. SegFormer will be a separate scene-segmentation evaluation.

## Setup (from repository root)

```bash
cd backend
uv sync --extra vision
```

`uv` creates `backend/.venv`. First execution downloads the official
`yoloe-26s-seg.pt` checkpoint and `mobileclip2_b.ts` text encoder if absent.
Run once online; reuse the downloaded files from the same directory afterward.
Use `--model /path/model.pt` for an existing text-prompt YOLOE checkpoint.
Official documentation: https://docs.ultralytics.com/models/yoloe/
The vision extra includes the official CLIP tokenizer pinned to a Git commit;
resolved Python dependencies are recorded in `backend/uv.lock`.

## Run (from backend)

```bash
PYTHONPATH=src uv run --extra vision python -m cane.benchmarks.yolo --source /absolute/path/route.mp4 --show
```

Also accepts a single image or a directory of images, sorted by filename without
recursion. For an optional live **YOLO-only** preview:

```bash
PYTHONPATH=src uv run --extra vision python -m cane.benchmarks.yolo --camera 0 --show
```

Press `q` to finish and save the summary. Camera/windows are released on exit.
Use recorded inputs for model comparisons: live camera scenes are not identical.
No images are uploaded.

On macOS the runner selects AVFoundation explicitly. Empty camera reads are
retried for up to 10 seconds (between backend calls), allowing Continuity Camera
to start; `--camera-timeout 20` extends that wait. Initial camera startup time is
reported separately as `camera_startup_ms`; the first camera sample has read time
zero. Later read/recovery delays remain part of measured read time. File EOF is
not retried. A backend call that blocks cannot be interrupted by this retry timer.

If the camera opens but supplies no frames, find a working index without loading
YOLOE:

```bash
PYTHONPATH=src .venv/bin/python -m cane.benchmarks.yolo --list-cameras --camera-timeout 3
```

Camera indices identify devices, not iPhone lenses, and may change. On the Mac,
allow Camera access for Terminal (or the terminal-hosting app) under System
Settings > Privacy & Security > Camera. Close competing camera apps. For
Continuity Camera, enable it on the iPhone, lock/mount the phone, and verify it
appears in FaceTime; close FaceTime before testing here. USB plus trusting the
Mac can help establish the connection. See Apple's setup/troubleshooting guide:
https://support.apple.com/en-us/102546

The `AVCaptureDeviceTypeExternal is deprecated` message comes from the native
camera-discovery path. It alone does not establish why frame delivery failed.
This retry change does not modify the Python application's Info.plist or replace
OpenCV's discovery implementation.

## Controlled comparison

```bash
PYTHONPATH=src uv run --extra vision python -m cane.benchmarks.yolo --source /absolute/path/route.mp4 --device mps --imgsz 640 --warmup 10 --max-frames 300 --output benchmark_runs/yoloe-small
PYTHONPATH=src uv run --extra vision python -m cane.benchmarks.yolo --source /absolute/path/route.mp4 --model yoloe-26n-seg.pt --device mps --imgsz 640 --warmup 10 --max-frames 300 --output benchmark_runs/yoloe-nano
```

Automatic preference: CUDA → MPS → CPU. Explicit unavailable devices fail.
This is **PyTorch, FP32 requested, batch one**, square letterboxing (`rect=False`).
It is **not Core ML/ANE performance**; that needs a separate runner/comparison.
Keep input, prompts, size, thresholds, device and precision fixed across comparisons.
Avoid other GPU workloads; record thermal conditions for sustained runs.

Default prompts: person, bicycle, scooter, car, bus, truck, chair, door, pole,
bollard, barrier, overhanging obstacle. Override with, for example,
`--classes person chair door pole wall "overhanging obstacle"`.
These are raw model labels, not the event contract's classes. A prompt does not
guarantee recognition. Walls/floors primarily belong to the SegFormer evaluation.

## Outputs

New directory per run under `backend/benchmark_runs/`; existing output directories
are rejected to avoid mixing results.

- `summary.json`: model/settings, versions, device, startup time, warmup count,
  p50/p95/mean timings, FPS, memory observations. `status=complete` means the run
  finished; `running` left behind means it did not finish.
- `timings.csv`: every measured frame, including zero-detection frames.
- `detections.jsonl`: frame index, decoded BGR pixel hash, input shape, raw labels,
  confidence, original-image `xyxy` boxes and original-pixel mask polygons.
  Video position is decoder-reported, **not sensor capture time**.
- `frame_*.jpg`: annotated samples; `--save-every 1` saves all, `0` saves none.

`predict_ms` is synchronized wall time around the complete predict call.
`preprocess_ms`, `inference_ms`, `postprocess_ms` wrap the predictor's stages
with explicit CUDA/MPS synchronization. This corrects missing MPS synchronization
in the installed Ultralytics internal timers. They may not sum to predict time
because predict also includes orchestration. Stage barriers add profiling overhead;
these are sequential component measurements, not maximum pipelined throughput.

`frame_ms = read_ms + predict_ms + result_ms`; result time includes copying boxes
and extracting polygons. **Processed FPS excludes drawing, hashing, file writes,
display, startup, downloads and warmup.** It is not display FPS or end-to-end
capture-to-feedback latency. `predict_fps` is separate. FPS is computed from total
time, not an average of instantaneous FPS. No recorded frames are dropped.

Warmup repeats the first image outside measured rows, then measures it as frame
zero. One input image produces one sample; use hundreds of representative frames
for meaningful p95 values. Process peak RSS includes startup; CUDA memory is peak
allocated, MPS memory is current allocated. Neither represents combined total
CPU/GPU memory. Mac timings do not establish iPhone performance.

Saved overlays allow inspection, **not an accuracy claim**. Label held-out routes
and count misses/false positives by class, splitting training/evaluation by location
and recording session. Inspect glass, thin poles, overhead obstacles, motion blur,
and poor lighting. Confidence scores are not measured reliability.

## Checks without downloading models

From `backend`:

```bash
PYTHONPATH=src python3 -m unittest discover -s tests/benchmarks -v
PYTHONPATH=src python3 -m cane.benchmarks.yolo --help
```

Future depth/SegFormer runners should reuse frame IDs/pixel hashes and summary
conventions. Camera age, networking and feedback delays remain separate metrics.
The existing event schema and transport are unchanged.
