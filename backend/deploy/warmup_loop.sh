#!/usr/bin/env bash
# Verified on an NVIDIA L4 (us-east1-d, 2026-09-26): built and run on the GPU; see deploy/README.md for the measured numbers.
# "Never let the demo be the cloud's first request." Needed locally too: the
# first MPS inference measured 11.1 s of graph compilation on this Mac.
set -euo pipefail
URL=${1:?usage: warmup_loop.sh http://host:8000}
while true; do
  curl -fsS --max-time 10 "$URL/warmup" >/dev/null || echo "$(date -u) warmup failed"
  sleep "${INTERVAL:-20}"
done
