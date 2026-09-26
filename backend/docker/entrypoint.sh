#!/usr/bin/env bash
# UNVERIFIED as of 2026-09-26: no GPU, no gcloud, no GCP project. Nothing here has been executed.
# Export the engine on THIS GPU if the cache does not match, then serve.
set -euo pipefail

python -m cane.cloud.export_engine --weights "${CANE_WEIGHTS_PATH:?}" --out "${CANE_ENGINE_PATH:?}"

# --workers 1 is a correctness requirement, not tuning: two worker processes
# split the tracker registry, so one physical object gets different track_ids
# on alternating frames and id stability dies silently.
exec uvicorn cane.cloud.app:app --host 0.0.0.0 --port "${CANE_PORT:-8000}" \
     --workers 1 --timeout-keep-alive 30 --log-level "${CANE_LOG_LEVEL:-info}"
