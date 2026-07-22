#!/bin/bash
# Start owner_v16_tipuni_cold on Windows 3060 (WMI). Does NOT promote.
# Owner ruling 2026-07-23: COLD START ONLY (yolo11n; v12-lineage never a base).
# train_dense.py auto-disables the finetune lr recipe for yolo11*.pt bases and
# keeps SAFE_AUG (no flips/mosaic/hsv). oomfix settings retained: batch 8,
# workers 2 (epoch-1 val ap_per_class MemoryError on 16GB hosts).
set -euo pipefail
cd "$(dirname "$0")/.."
HOST="${FABLE_3060_HOST:-zzc@192.168.1.3}"
NAME="${NAME:-owner_v16_tipuni_cold}"
BASE="${BASE:-yolo11n.pt}"
BATCH="${BATCH:-8}"
WORKERS="${WORKERS:-2}"
EPOCHS="${EPOCHS:-60}"
PATIENCE="${PATIENCE:-15}"

ssh -o BatchMode=yes "$HOST" "Test-Path C:/fable/models/$BASE" | tr -d '\r' | grep -qi true \
  || { echo "missing C:/fable/models/$BASE — run sync_v16_to_windows.sh"; exit 1; }

ssh -o BatchMode=yes "$HOST" "New-Item -ItemType Directory -Force -Path C:\fable\logs | Out-Null; \$cmd='cmd.exe /c cd /d C:\fable && C:\fable\.venv\Scripts\python.exe -u C:\fable\train_dense.py --name $NAME --model C:/fable/models/$BASE --dataset C:/fable/datasets/dense_owner_v16_tipuni --epochs $EPOCHS --patience $PATIENCE --batch $BATCH --cache false --workers $WORKERS > C:\fable\logs\\$NAME.log 2>&1'; Invoke-CimMethod -ClassName Win32_Process -MethodName Create -Arguments @{CommandLine=\$cmd} | Out-Null; Write-Output started_base=$BASE name=$NAME epochs=$EPOCHS batch=$BATCH workers=$WORKERS"
echo "Watch:  ssh $HOST \"Get-Content C:\\fable\\logs\\$NAME.log -Tail 30\""
