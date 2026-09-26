#!/usr/bin/env bash
# UNVERIFIED as of 2026-09-26: no GPU, no gcloud, no GCP project. Nothing here has been executed.
# VM startup script referenced by create_l4_vm.sh: mount the persistent engine
# cache, then run the container with all GPUs.
set -euo pipefail

: "${CANE_IMAGE:?full Artifact Registry image ref}"
DEVICE=${CANE_MODELS_DEVICE:-/dev/disk/by-id/google-models}
MOUNT=${CANE_MODELS_MOUNT:-/mnt/models}

mkdir -p "$MOUNT"
if ! mountpoint -q "$MOUNT"; then
  if ! blkid "$DEVICE" >/dev/null 2>&1; then
    # Deliberately opt-in. This formats a block device; an unguarded mkfs in a
    # script a teammate might run by hand is not worth the three saved seconds.
    if [ "${CANE_FORMAT_DISK:-0}" != "1" ]; then
      echo "$DEVICE has no filesystem. Re-run with CANE_FORMAT_DISK=1 to format it." >&2
      exit 1
    fi
    mkfs.ext4 -F "$DEVICE"
  fi
  mount "$DEVICE" "$MOUNT"
fi

docker run -d --restart always --gpus all -p 8000:8000 \
  -v "$MOUNT":/models \
  -e CANE_API_KEY="${CANE_API_KEY:-}" \
  --name cane "$CANE_IMAGE"
