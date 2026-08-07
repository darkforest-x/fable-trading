#!/bin/bash
# P1 cold-start: Stage-B causal local_signal_v2 on 3060.
# Wrapper around train_w20_midbox_on_3060.sh with Stage-B paths.
# Never promotes ACTIVE / owner_best.
set -euo pipefail
cd "$(dirname "$0")/.."
HOST="${FABLE_3060_HOST:-zzc@192.168.1.4}"
export FABLE_3060_HOST="$HOST"
exec bash scripts/train_w20_midbox_on_3060.sh \
  --dataset datasets/local_signal_v2_stageb \
  --name owner_lsv2_stageb_cold \
  --epochs 60 \
  --patience 15 \
  --batch 8 \
  --host "$HOST" \
  "$@"
