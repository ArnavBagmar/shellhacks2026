# Cloud L4 deployment

> **UNVERIFIED as of 2026-09-26: no GPU, no gcloud, no GCP project. Nothing in
> this directory, in `../docker/`, or in `cane/cloud/export_engine.py` has been
> executed.** It is written from the design, not from a run. The two acceptance
> criteria "the engine loads on a fresh L4 boot" and "inference under 25 ms" are
> **deferred** and are only verifiable by `python -m cane.cloud.smoke` once a VM
> exists. What *has* run, on a MacBook Pro M2 Pro against a real Insta360 X4, is
> the same service with `CANE_DETECTOR=ultralytics CANE_DEVICE=mps`: 177 frames
> at rung 1, `inference_ms` p50 178 ms / p95 369 ms, 2-5 detections per frame,
> one `track_id` stable across 164 consecutive frames. See
> `docs/steps/assets/step-07-live-detections.jpg`.

## Build and push (us-east1)

```sh
REGION=us-east1; PROJECT=<project>; REPO=cane
gcloud artifacts repositories create "$REPO" --repository-format=docker --location="$REGION"
docker build -f backend/docker/Dockerfile -t "$REGION-docker.pkg.dev/$PROJECT/$REPO/cane-cloud:v1" backend
docker push "$REGION-docker.pkg.dev/$PROJECT/$REPO/cane-cloud:v1"
```

Build on an **amd64** host. The image is ~20 GB (NGC PyTorch); the first push
is slow, later pushes are layer-deltas.

## Create the VM

```sh
ALLOWED_CIDR=<laptop public ip>/32 PROJECT=<project> \
  IMAGE=us-east1-docker.pkg.dev/<project>/cane/cane-cloud:v1 \
  ./deploy/create_l4_vm.sh
```

`ALLOWED_CIDR` has **no default**. An open port to a GPU VM that decodes
attacker-supplied JPEGs is the one security hole this step can create. Set
`CANE_API_KEY` in the container as well if the venue network is shared.

`g2-standard-8` + one L4 in `us-east1-b` (South Carolina, ~20-30 ms RTT from
Florida). A 50 GB persistent disk is mounted at `/models` and carries **the
TensorRT engine and its sidecar, and nothing else**; the first boot on a
fresh disk formats it only if you pass `CANE_FORMAT_DISK=1`.

`/models` is a bind mount, so it *hides* whatever the image baked under it.
The `yolo11s.pt` weights are therefore baked at `/opt/models/yolo11s.pt`
(`CANE_WEIGHTS_PATH`) and the ultralytics config at `/opt/ultralytics` — both
outside the mount. Baked under `/models`, they would vanish behind an empty
disk on the first boot and ultralytics would silently re-download the weights
from GitHub: no error, just a slow start and a hard dependency on the venue
wifi. `CANE_ENGINE_PATH` stays on the disk, which is the whole point of it.

## Cold start expectations

| stage | first boot | later boots |
|---|---|---|
| VM create + image pull | 3-6 min | 1-2 min |
| TensorRT export (`export_engine.py`) | **3-5 min** | skipped (cache hit) |
| first inference at each shape | one-off reshape | one-off reshape |

Run `./deploy/warmup_loop.sh http://<ip>:8000` from the moment you walk toward
the stage. It warms **both** 640 and 512, because one dynamic engine serves
both rungs and pays a context reshape at each new shape. Never poll `/warmup`
as a health check — it takes the inference lock. Step 8 polls `/healthz`.

## Smoke test

```sh
python -m cane.cloud.smoke --url http://<ip>:8000 --image docs/steps/assets/step-06-live-equirect.jpg
```

Exit 0 only if `engine_ready`, every request is 200, and the median
`inference_ms` over frames 2..N is within 25 ms. Exit 2 = engine not ready,
3 = HTTP error, 4 = too slow, 5 = no frames. Use `--max-inference-ms 200` for
a local MPS server: 25 ms is the L4 number.

## Engine caching, and why it is a Python step

`export_engine.py` writes `${CANE_ENGINE_PATH}.json` next to the engine with
the GPU name, TensorRT version, ultralytics version, `imgsz` and `batch`. A
volume re-attached to a different accelerator therefore rebuilds instead of
serving an engine that cannot deserialize — which *is* the acceptance
criterion. The engine is written to `.tmp` and `os.replace`d into place, so an
OOM-killed export never leaves a truncated file behind.

One **dynamic** engine is exported at `imgsz=640, batch=12`: the optimisation
profile then sits at `opt=(6,3,640,640)`, exactly our real batch, and the
`min=(1,3,32,32)` bound covers 512 too.

**Escape hatch:** if the dynamic export fails on the L4, re-export static and
pin the bridge to rungs 1-2:

```sh
yolo export model=yolo11s.pt format=engine imgsz=640 batch=6 dynamic=False half=True device=0
```

`device=0` is mandatory either way — on CPU the exporter disables `half` and
then asserts `not dynamic`.

## Two things that must not change

- `--workers 1`. Two worker processes split the tracker registry, so one
  physical object gets different `track_id`s on alternating frames and id
  stability dies silently. This is correctness, not tuning.
- Do **not** install the project's `gpu` extra in the image. PyPI `tensorrt`
  resolves to a CUDA-13 build that would layer a second TensorRT over the NGC
  image's own.
