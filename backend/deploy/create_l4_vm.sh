#!/usr/bin/env bash
# UNVERIFIED as of 2026-09-26: no GPU, no gcloud, no GCP project. Nothing here has been executed.
set -euo pipefail

# An open port to a GPU VM that decodes attacker-supplied JPEGs is the one
# security hole this step can actually create. No default, ever.
: "${ALLOWED_CIDR:?set it to the public /32 of the demo laptop, e.g. 203.0.113.7/32}"
PROJECT=${PROJECT:?gcloud project id}
IMAGE=${IMAGE:?full Artifact Registry image ref}
ZONE=${ZONE:-us-east1-b}     # closest GCP region to Florida, ~20-30 ms RTT
NAME=${NAME:-cane-l4}
PORT=${PORT:-8000}
DISK=${DISK:-cane-models}    # persistent engine cache, survives a recreate
HERE=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)

gcloud compute disks create "$DISK" --project "$PROJECT" --zone "$ZONE" \
  --size 50GB --type pd-balanced || echo "disk $DISK already exists"

gcloud compute instances create "$NAME" \
  --project "$PROJECT" --zone "$ZONE" \
  --machine-type g2-standard-8 \
  --accelerator type=nvidia-l4,count=1 \
  --maintenance-policy TERMINATE --restart-on-failure \
  --image-family common-cu123-debian-11 \
  --image-project deeplearning-platform-release \
  --boot-disk-size 100GB \
  --disk "name=$DISK,device-name=models,mode=rw,auto-delete=no" \
  --metadata "CANE_IMAGE=$IMAGE" \
  --metadata-from-file "startup-script=$HERE/startup.sh" \
  --tags cane-inference

gcloud compute firewall-rules create "${NAME}-inference" \
  --project "$PROJECT" --allow "tcp:$PORT" \
  --source-ranges "$ALLOWED_CIDR" --target-tags cane-inference \
  || echo "firewall rule already exists"

echo "created $NAME in $ZONE; port $PORT reachable only from $ALLOWED_CIDR"
echo "smoke it:  python -m cane.cloud.smoke --url http://<external-ip>:$PORT"
