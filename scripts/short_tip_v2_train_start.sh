#!/bin/bash
# Start owner_side_short_tip_v2 on the Windows 3060, detached from SSH.
#
# Why WMI instead of a plain `ssh ... python train_dense.py`: a foreground SSH
# run died mid-save with "ValueError: I/O operation on closed file" inside
# torch.save (2026-07-27) -- when the SSH channel hiccups, stdout closes under
# the writer and the checkpoint is corrupted. Win32_Process Create detaches the
# trainer from the session and redirects its output to a file on the Windows
# side, which is the pattern v16_train_start.sh / train_owner_hts.sh already use.
#
# --min-train 500 is deliberate, not a workaround for a bad copy: v2 keeps only
# boxes that are genuinely dense at the re-anchored tip, so 571 train images is
# the designed size (v1 had 1037 but only ~1.4% sat on a dense bar). The remote
# copy was verified at 765 images before launching.
#
# Cold start from yolo11n per the owner's 2026-07-23 ruling (no v12-lineage base).
# Does NOT promote. Weights are fetched and judged on Mac.
#
# Usage:
#   bash scripts/short_tip_v2_train_start.sh
#   ssh zzc@192.168.1.5 "Get-Content C:\fable\logs\owner_side_short_tip_v2.log -Tail 30"
set -euo pipefail

HOST="${FABLE_3060_HOST:-zzc@192.168.1.5}"
NAME="owner_side_short_tip_v2"
DATASET="C:/fable/datasets/dense_owner_side_short_tip_v2"
BASE="C:/fable/models/yolo11n.pt"
EPOCHS="${EPOCHS:-100}"
PATIENCE="${PATIENCE:-20}"
BATCH="${BATCH:-8}"
WORKERS="${WORKERS:-4}"
MIN_TRAIN="${MIN_TRAIN:-500}"

ssh -o BatchMode=yes "$HOST" "Test-Path $BASE" | tr -d '\r' | grep -qi true \
  || { echo "missing $BASE on 3060"; exit 1; }
ssh -o BatchMode=yes "$HOST" "Test-Path $DATASET/images/train" | tr -d '\r' | grep -qi true \
  || { echo "missing dataset on 3060: $DATASET"; exit 1; }

ssh -o BatchMode=yes "$HOST" "New-Item -ItemType Directory -Force -Path C:\fable\logs | Out-Null; \$cmd='cmd.exe /c cd /d C:\fable && C:\fable\.venv\Scripts\python.exe -u C:\fable\train_dense.py --name $NAME --model $BASE --dataset $DATASET --epochs $EPOCHS --patience $PATIENCE --batch $BATCH --min-train $MIN_TRAIN --cache false --workers $WORKERS > C:\fable\logs\\$NAME.log 2>&1'; Invoke-CimMethod -ClassName Win32_Process -MethodName Create -Arguments @{CommandLine=\$cmd} | Out-Null; Write-Output started=$NAME epochs=$EPOCHS batch=$BATCH"

echo "Watch:  ssh $HOST \"Get-Content C:\\fable\\logs\\$NAME.log -Tail 30\""
