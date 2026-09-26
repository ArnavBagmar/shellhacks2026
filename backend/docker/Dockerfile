# UNVERIFIED as of 2026-09-26: no GPU, no gcloud, no GCP project. Nothing here has been executed.
# Base image supplies torch AND TensorRT. Do NOT install the project's `gpu`
# extra here: PyPI `tensorrt` resolves to a CUDA-13 build that would install a
# second, conflicting TensorRT over the image's own (design A11).
ARG NGC_TAG=25.08-py3
FROM nvcr.io/nvidia/pytorch:${NGC_TAG}

# The project depends on opencv-python (not -headless) because the bridge
# needs the GUI build on the Mac; one apt line is cheaper than splitting the
# dependency across two distributions (design A15.4).
RUN apt-get update \
 && apt-get install -y --no-install-recommends libgl1 libglib2.0-0 \
 && rm -rf /var/lib/apt/lists/*

# /models is a BIND MOUNT (deploy/startup.sh mounts the persistent disk
# there), so on a fresh disk it hides everything baked under it. Only the
# engine and its sidecar -- the things that must survive a recreate and cost
# 3-5 min to rebuild -- live there. Everything baked at image build time goes
# under /opt, or the container quietly re-downloads it from GitHub on first
# boot and the demo dies on a rate-limited venue wifi.
ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    YOLO_CONFIG_DIR=/opt/ultralytics \
    CANE_DETECTOR=tensorrt \
    CANE_DEVICE=0 \
    CANE_WEIGHTS_PATH=/opt/models/yolo11s.pt \
    CANE_ENGINE_PATH=/models/yolo11s_b12dyn_fp16.engine \
    CANE_PORT=8000

WORKDIR /app
COPY pyproject.toml README.md ./
COPY src ./src

# --no-deps for ultralytics so its torch/opencv pins cannot downgrade the
# image's CUDA-matched builds. The rest are the project's base deps, listed
# explicitly because the `gpu` extra must never be installed here.
RUN pip install --no-deps "ultralytics>=8.3" \
 && pip install fastapi "uvicorn[standard]" python-multipart \
      "supervision>=0.25,<0.31" pydantic-settings "pydantic>=2.7" \
      opencv-python httpx \
 && pip install --no-deps -e .

# Bake the weights in, OUTSIDE the /models mount: a fresh boot must never
# depend on a GitHub asset download, and `settings sync=False` keeps
# ultralytics from phoning home. /models is created empty as the mount point.
RUN mkdir -p /opt/models /opt/ultralytics /models \
 && python -c "from ultralytics import YOLO; YOLO('/opt/models/yolo11s.pt')" \
 && yolo settings sync=False

COPY docker/entrypoint.sh /usr/local/bin/entrypoint.sh
RUN chmod +x /usr/local/bin/entrypoint.sh

EXPOSE 8000
# The engine is exported at container start, on the GPU it will serve on, and
# cached to the /models volume. Engines are not portable across GPU
# architectures, which is why this cannot happen at image build time.
ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]
