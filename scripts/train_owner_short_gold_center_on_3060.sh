#!/bin/bash
# Train the first Owner-short center-crop baseline on the LAN RTX 3060.
#
# This is deliberately the 1:1 easy-negative arm from the V1 handoff.  Its only
# purpose is to produce a frozen baseline for hard-negative mining.  It never
# reads holdout, promotes ACTIVE/owner_best, or deploys a model.
set -euo pipefail
cd "$(dirname "$0")/.."

HOST="${FABLE_3060_HOST:-zzc@192.168.1.4}"
export FABLE_3060_HOST="$HOST"

exec bash scripts/train_w20_midbox_on_3060.sh \
  --dataset datasets/owner_short_gold_center_v1 \
  --base analysis/output/lsv2_stagea/owner_lsv2_stagea_randomcrop_v1_cold/weights/best.pt \
  --name owner_lsv2_short_gold_center_v1_ft \
  --epochs 40 \
  --patience 10 \
  --batch 8 \
  --seed 0 \
  --finetune \
  --host "$HOST" \
  "$@"
