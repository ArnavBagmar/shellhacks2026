# Verified on an NVIDIA L4 (us-east1-d, 2026-09-26): built and run on the GPU; see deploy/README.md for the measured numbers.
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
# "numpy<2" is load-bearing (verified on an L4, 2026-09-26): the NGC torch is
# built against NumPy 1.x, and letting opencv/supervision pull NumPy 2 breaks
# torch's NumPy bridge at predict time ("RuntimeError: Numpy is not available")
# and destabilises the TensorRT export. opencv-python 5 declares numpy>=2, so it
# is pinned to 4.x alongside. Pin both last so nothing re-upgrades them.
RUN pip install --no-deps "ultralytics>=8.3" \
 && pip install fastapi "uvicorn[standard]" python-multipart \
      "supervision>=0.25,<0.31" pydantic-settings "pydantic>=2.7" \
      "opencv-python>=4.10,<5" httpx \
 && pip install --no-deps -e . \
 && pip install "numpy<2" "opencv-python>=4.10,<5" \
 && python -c "import numpy, torch; assert numpy.__version__.startswith('1.'), numpy.__version__; torch.from_numpy(numpy.zeros(1))"

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
