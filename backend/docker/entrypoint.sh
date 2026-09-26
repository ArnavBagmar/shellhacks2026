#!/usr/bin/env bash
# Verified on an NVIDIA L4 (us-east1-d, 2026-09-26): built and run on the GPU; see deploy/README.md for the measured numbers.
# Export the engine on THIS GPU if the cache does not match, then serve.
set -euo pipefail

python -m cane.cloud.export_engine --weights "${CANE_WEIGHTS_PATH:?}" --out "${CANE_ENGINE_PATH:?}"

# --workers 1 is a correctness requirement, not tuning: two worker processes
# split the tracker registry, so one physical object gets different track_ids
# on alternating frames and id stability dies silently.
exec uvicorn cane.cloud.app:app --host 0.0.0.0 --port "${CANE_PORT:-8000}" \
     --workers 1 --timeout-keep-alive 30 --log-level "${CANE_LOG_LEVEL:-info}"
