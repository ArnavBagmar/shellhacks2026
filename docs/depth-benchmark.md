# Standalone Depth Anything V2 Small

Runs official relative-depth Small (`vits`) independently of YOLOE. No server,
object detector, scene segmenter, metric distance, navigation warning or calibration.
The existing YOLOE runner and its user-edited categories are unchanged.

For **detections and depth drawn together on one frame**, use the fused preview
instead: [`fused-preview.md`](fused-preview.md). This runner stays as the
depth-only baseline, since a fused timing cannot be compared to a single-model one.

## Setup from a fresh clone

From the repository root:

```bash
cd backend
uv sync --extra vision --extra depth
mkdir -p third_party checkpoints
git clone https://github.com/DepthAnything/Depth-Anything-V2.git third_party/Depth-Anything-V2
git -C third_party/Depth-Anything-V2 checkout a561b849ebae10a6f5ef49e26c83cbbcd36c71bf
curl -L --fail --retry 2 https://huggingface.co/depth-anything/Depth-Anything-V2-Small/resolve/main/depth_anything_v2_vits.pth -o checkpoints/depth_anything_v2_vits.pth
```

Skip cloning/downloading if already set up. `depth` alone is sufficient for this
runner; keep both extras when also using YOLOE so `uv sync` retains its packages.
The source checkout and ~95 MiB checkpoint are ignored by Git. The source revision
is pinned and checked at startup; weights load strictly into the Small model with
`weights_only=True`, and their SHA256 is written to each run's summary. This is
PyTorch FP32, not a Core ML or iPhone performance claim.

## Live preview (from backend)

```bash
PYTHONPATH=src .venv/bin/python -m cane.benchmarks.depth --camera 0 --show --max-frames 0
```

Use `--camera 1` if that is your iPhone. Probe indices without loading a model:

```bash
PYTHONPATH=src .venv/bin/python -m cane.benchmarks.yolo --list-cameras
```

CUDA → MPS → CPU is selected automatically. `--device cpu|mps|cuda` forces a device;
unavailable devices fail explicitly. The camera uses the same Continuity startup
retry as YOLOE. The original view stays on the left, aligned depth/overlay on the
right. Use the **Overlay %** trackbar; 35% is the initial opacity.

Every toggle is both a labelled toolbar button stating `ON`/`off` and a key;
clicking the button and pressing the key do the same thing.

- `Overlay` / `d`: switch between overlay and depth-only right panel.
- `Corridor` / `c`: toggle the central-third guide lines (visual reference only).
- `s`: save the current preview and raw depth array.
- `q` / `Q` / Esc, the red **Quit** button, or the window close button: stop and
  write the timing summary. Click the preview to focus it before keyboard input.
  Ctrl-C in the terminal during the measured loop also saves the summary.
- `--max-frames 0`: unlimited until quit or input EOF. Default is 300 frames.
- `--save-every 0`: disable automatic sample saving (default every 30 frames).

The OpenCV UI stays on the main thread and pumps input about every 10 ms while a
single worker performs camera reads, warmup, inference and sample writes. Slider
changes redraw the latest completed frame immediately; they do not wait for a new
prediction. Moving the slider also restores overlay mode if `d` selected heatmap-only.
There is still only one processing operation in flight, with no frame/model overlap.
Quit closes the window promptly, then waits for the current native operation to
finish safely before releasing the camera and writing the summary. Camera retry
loops are cancellable; an already-blocked native driver/GPU call cannot be forcibly
interrupted. `s` saves the frame displayed at the keypress, even during inference.
UI buffers are resized separately; raw depth and inference inputs retain their
original dimensions. Headless benchmark execution remains sequential on one thread.

## Meaning of the depth display

Raw output is an H×W floating-point NumPy array **at the original image size**:
`depth[y, x]` corresponds to image row y, column x. Each frame's shape is asserted
at runtime. It is relative inverse-depth-like output: larger means nearer, not
meters. No object classes are required: depth covers the entire image.

For display we compute, independently for each frame:

```
relative = (raw - raw.min()) / (raw.max() - raw.min())
```

A value of 0 is the far end and 1 the near end **of this frame's estimated range**.
The palette goes from **light green (farther) to dark orange (nearer)**, darkening
as it approaches the near end; it is shared with the fused preview so the same
colour means the same thing in both. The center label is the median over a central
10%-width × 10%-height rectangle, shown in white.
A flat map produces zeros and an explicit `flat` flag, not a meaningful distance;
nonfinite maps fail rather than displaying fabricated values.

Per-frame normalization can change when another object enters the image. Moving
an object closer need not monotonically increase its normalized number across
frames (especially if it was already the nearest object). Inspect relative
ordering against other objects and boundaries. This single-image model has no
explicit temporal stabilization. A colored heatmap is not evidence of accuracy.

## Resolution and performance comparison

The preview defaults to **280**, deliberately smaller than upstream's **518**, to
make an initial local speed test practical. The official resize keeps aspect ratio
and rounds network dimensions to multiples of 14. `280` does not mean 280×280;
a 1920×1080 input becomes approximately 504×280. Actual network shapes are logged.
Lower resolution loses fine detail; compare identical recordings at both sizes:

```bash
PYTHONPATH=src .venv/bin/python -m cane.benchmarks.depth --source /absolute/path/route.mp4 --input-size 280 --warmup 10 --max-frames 300 --output benchmark_runs/depth-280
PYTHONPATH=src .venv/bin/python -m cane.benchmarks.depth --source /absolute/path/route.mp4 --input-size 518 --warmup 10 --max-frames 300 --output benchmark_runs/depth-518
```

File frames are not dropped, and IDs/pixel hashes match the YOLOE runner on the
same decoded inputs. Different models need not use identical internal sizes, but
record each size and evaluate its quality alongside speed. Image directories are
sorted by filename. A single image gives only one timing sample.

## Timings and artifacts

`benchmark_runs/depth-*/summary.json` records settings, source revision, checkpoint
hash, versions, network shapes, memory observations and mean/p50/p95 timings.
`timings.csv` contains all measured frames; `frames.jsonl` contains original image
hashes, output shapes, normalization bounds and center readings. Saved samples
pair `frame_NNNNNN.jpg` with **raw, unnormalized** `depth_NNNNNN.npy` arrays.

- `preprocess_ms`: official BGR→RGB, normalization, aspect-preserving resize,
  tensor preparation and device transfer.
- `inference_ms`: synchronized neural-network forward execution.
- `postprocess_ms`: remainder of `predict_ms`, primarily interpolation back to
  original H×W and CPU array transfer, plus wrapper overhead.
- `predict_ms`: complete synchronized official `infer_image` call.
- `result_ms`: finite checks, display normalization and center median.
- `frame_ms`: read + predict + result. Drawing, hashing, display and disk I/O are
  excluded. This is not display FPS or capture-to-feedback latency.

Warmup and model loading are excluded. Initial camera read is recorded separately
and contributes zero to frame-zero read time; later read/retry delays are measured.
MPS current allocation and CUDA peak allocation are different metrics; process
peak RSS includes startup and is not total CPU+GPU memory.

## Implementation details and verification

The runner calls the official `infer_image` unchanged. A small subclass preserves
its preprocessing transforms but transfers input to the **requested device**:
upstream `image2tensor` auto-selects CUDA/MPS even when the model is explicitly on
CPU. We time preprocessing/forward with explicit accelerator synchronization;
interpolation/CPU conversion stay upstream. Stage barriers add profiling overhead.

Runtime output-shape checks plus unit tests cover normalization, flat/invalid
maps, center coordinates, and missing source. Real-model checks should compare
this adapter to the original class on the same device and pixels. For qualitative
near/far validation, inspect known foreground/background regions in an image;
then test locally with a chair in front of a person, motion and different lighting.
This is not a calibrated distance or accuracy evaluation.

Official sources:
- https://github.com/DepthAnything/Depth-Anything-V2
- https://huggingface.co/depth-anything/Depth-Anything-V2-Small

## Checks executed during implementation

- 119 Python tests passed (including existing contract/transport tests).
- Real Small checkpoint on Apple MPS: 12-frame synthetic clip, alternating the
  Ultralytics bus sample and black frames; 5 warmups, input-size 280. Mean complete
  prediction 46.53 ms, p95 49.20 ms. This short clip is a functional smoke test,
  not representative sustained FPS or accuracy evidence.
- Wrapper versus unmodified official `infer_image`, same MPS device/sample at 280:
  maximum absolute difference **0.0**; output **1080×810**, network **1×3×378×280**.
- Sample foreground-person median raw value **3.9351**, distant-building region
  **0.5740**: larger values corresponded to the visibly nearer region. This is one
  qualitative direction check, not a distance calibration.
- Explicit CPU selection succeeded on the same MPS-capable host.
- All 12 depth frame IDs/pixel hashes matched the preceding YOLOE test run;
  saved raw arrays had original-image dimensions and finite values.
- Checkpoint SHA256:
  `715fade13be8f229f8a70cc02066f656f2423a59effd0579197bbf57860e1378`.
- The depth live-camera window/controls still need local testing. `xFormers not
  available` is an optional-acceleration message; the tested model runs without it.
