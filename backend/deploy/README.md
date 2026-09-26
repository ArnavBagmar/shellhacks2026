# Cloud L4 deployment

> **Verified on the L4, 2026-09-26.** The image was built and served on
> `l4-infer` (`g2-standard-4` + 1× NVIDIA L4, `us-east1-d`, Deep Learning VM
> image with driver 580). Measured from the venue network with the real X4
> bridge: **round trip p50 72 ms / p95 85–93 ms at 3 fps, zero failures**, in
> TensorRT mode; the model runs in **12 ms at 640 and 20 ms at 512** (warm
> `/warmup`), and the service's decode-inclusive `inference_ms` is ~30 ms.
> PyTorch-on-CUDA mode (no engine) measured 42 ms model / ~59 ms service and
> round trip p50 91 ms. `create_l4_vm.sh` and `startup.sh` were **not executed**
> — the VM pre-existed and the steps below were run by hand.

## What was actually done on 2026-09-26 (repeatable)

1. VM `l4-infer` already existed (created in the console). Attached a 50 GB
   `pd-balanced` disk `cane-models` as device `models`, tagged the VM
   `cane-inference`, and created firewall rule `cane-inference` (tcp:8000 from
   the demo laptop's public /32 only). The external IP is ephemeral: it changes
   on stop/start unless you reserve it.
2. On the VM (no Docker on the DLVM image): `apt-get install docker.io`,
   `nvidia-ctk runtime configure --runtime=docker`, `systemctl restart docker`;
   `mkfs.ext4` the disk, mount at `/mnt/models`, add an `fstab` line.
3. Copied `backend/` (plus one test image) to the VM and built the image **on
   the VM** (amd64): `docker build -f backend/docker/Dockerfile -t cane-cloud:v3 backend`
   (~25 min the first time: 11 GB base pull + pip + weights bake).
4. Two findings that are now baked into the Dockerfile: **`numpy<2` and
   `opencv-python<5`** (the NGC torch is built against NumPy 1.x; NumPy 2 made
   `predict` fail with "Numpy is not available" and destabilised the TensorRT
   build), and a **14 GiB builder workspace**. The dynamic export still failed
   on TensorRT 10.13 ("could not find any implementation" for the attention
   block), so `export_engine.py` now falls back to a **static batch-6 640
   engine** (built in 323 s), which ultralytics letterboxes 512 crops into —
   both rung sizes work.
5. Production container (the exact command; `--restart always` survives reboots):

```sh
sudo docker run -d --restart always --gpus all -p 8000:8000 -v /mnt/models:/models \
  -e CANE_DETECTOR=tensorrt -e CANE_DEVICE=0 \
  -e CANE_ENGINE_PATH=/models/yolo11s_b12dyn_fp16.engine \
  --name cane cane-cloud:v3
```

   With the engine + sidecar on the disk the entrypoint reuses the cache and
   serves within ~20 s. On a fresh disk it exports (dynamic attempt, then the
   static fallback: ~9 min) once. Then `curl http://<ip>:8000/warmup` twice
   (first call ≈ 2–5 s per shape) and keep `deploy/warmup_loop.sh` running.

6. Day-to-day: `gcloud compute instances start|stop l4-infer --zone us-east1-d`
   (~$0.7–1/h while running — stop it when not testing); the engine cache and
   the container survive a stop/start; check
   `curl http://<new-ip>:8000/healthz`, then point the bridge at
   `--inference-url http://<new-ip>:8000/infer`.

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
