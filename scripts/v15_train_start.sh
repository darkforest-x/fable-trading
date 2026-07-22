#!/bin/bash
# Start owner_v15_tipval on Windows 3060 (WMI). Does NOT promote.
# 2026-07-22 oomfix: same tipval data; workers 4→2, batch 16→8; train_dense
# forces plots/save_json off + max_det=100 (epoch1 val ap_per_class MemoryError).
set -euo pipefail
cd "$(dirname "$0")/.."
HOST="${FABLE_3060_HOST:-zzc@192.168.1.3}"
NAME="${NAME:-owner_v15_tipval_oomfix}"
BASE=owner_v12_htip.pt
BATCH="${BATCH:-8}"
WORKERS="${WORKERS:-2}"
if ! ssh -o BatchMode=yes -o ConnectTimeout=10 "$HOST" "Test-Path C:/fable/models/$BASE" | tr -d '\r' | grep -qi true; then
  BASE=owner_best.pt
fi

ssh -o BatchMode=yes "$HOST" "New-Item -ItemType Directory -Force -Path C:\fable\logs | Out-Null; \$cmd='cmd.exe /c cd /d C:\fable && C:\fable\.venv\Scripts\python.exe -u C:\fable\train_dense.py --name $NAME --model C:/fable/models/$BASE --dataset C:/fable/datasets/dense_owner_v15_tipval --epochs 40 --patience 10 --batch $BATCH --cache false --workers $WORKERS > C:\fable\logs\\$NAME.log 2>&1'; Invoke-CimMethod -ClassName Win32_Process -MethodName Create -Arguments @{CommandLine=\$cmd} | Out-Null; Write-Output started_base=$BASE name=$NAME batch=$BATCH workers=$WORKERS"
echo "Watch: ssh $HOST \"Get-Content C:\\fable\\logs\\$NAME.log -Tail 30\""
echo "Status: NAME=$NAME bash scripts/v15_train_status.sh"
