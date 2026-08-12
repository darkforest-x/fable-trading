#!/bin/bash
# Train a local-signal YOLO dataset on LAN RTX 3060 (detached WMI).
#
# Supports both cold starts and explicit chain fine-tunes; never infer a chain
# run from the fixed remote upload filename.
# The repository-owned trainer is copied on every run so the augmentation
# policy is auditable and cannot drift from an untracked 3060 script.
#
# Usage:
#   export FABLE_3060_HOST=zzc@192.168.1.X   # current 3060 IP
#   bash scripts/train_w20_midbox_on_3060.sh --check
#   bash scripts/train_w20_midbox_on_3060.sh
#   bash scripts/train_w20_midbox_on_3060.sh --status
#   bash scripts/train_w20_midbox_on_3060.sh --base path/to/best.pt --finetune
set -euo pipefail
cd "$(dirname "$0")/.."

HOST="${FABLE_3060_HOST:-}"
REMOTE="C:/fable"
RPY="$REMOTE/.venv/Scripts/python.exe"
RUNS="$REMOTE/runs/detect"
DATASET="datasets/dense_owner_w20_midbox"
BASE="models/yolo11s.pt"
NAME="owner_w20_midbox_cold"
EPOCHS=80
PATIENCE=20
BATCH=8
WORKERS=2
SEED=0
FINETUNE_ARG=""
MODE="run"

# Every remote command below is PowerShell, but the 3060's sshd DefaultShell is
# cmd.exe (confirmed 2026-08-12). Sent bare, those commands fail *and* leave the
# exec channel open, so the client hangs instead of erroring -- which looks from
# here like an unreachable machine. ssh_ps.sh invokes powershell.exe explicitly
# and passes the body as -EncodedCommand so quoting survives.
# See docs/learnings/windows-ssh-default-shell-change-looks-like-an-unreachable-host.md
SSH=(bash "$(dirname "${BASH_SOURCE[0]}")/ssh_ps.sh")
SCP=(scp -o BatchMode=yes -o ConnectTimeout=15 -q)

say() { printf '\n\033[1;36m=== %s ===\033[0m\n' "$*"; }
die() { printf '\033[1;31m[X] %s\033[0m\n' "$*" >&2; exit 1; }

while [[ $# -gt 0 ]]; do
  case "$1" in
    --check) MODE=check; shift ;;
    --status) MODE=status; shift ;;
    --name) NAME="$2"; shift 2 ;;
    --dataset) DATASET="$2"; shift 2 ;;
    --base) BASE="$2"; shift 2 ;;
    --epochs) EPOCHS="$2"; shift 2 ;;
    --patience) PATIENCE="$2"; shift 2 ;;
    --batch) BATCH="$2"; shift 2 ;;
    --seed) SEED="$2"; shift 2 ;;
    --finetune) FINETUNE_ARG="--finetune"; shift ;;
    --no-finetune) FINETUNE_ARG="--no-finetune"; shift ;;
    --host) HOST="$2"; shift 2 ;;
    -h|--help)
      sed -n '2,14p' "$0"; exit 0 ;;
    *) die "unknown arg: $1" ;;
  esac
done

[[ -n "$HOST" ]] || die "set FABLE_3060_HOST=zzc@<ip> (3060 IP drifts; do not guess)"
[[ "$NAME" =~ ^[A-Za-z0-9_.-]+$ ]] || die "unsafe run name: $NAME"

if [[ "$MODE" == "status" ]]; then
  say "status $NAME on $HOST"
  "${SSH[@]}" "$HOST" "Get-Process python* -ErrorAction SilentlyContinue | Select-Object Id,CPU,WorkingSet | Format-Table | Out-String -Width 200" | tr -d '\r'
  "${SSH[@]}" "$HOST" "if (Test-Path C:/fable/logs/$NAME.log) { Get-Content C:/fable/logs/$NAME.log -Tail 40 } else { 'no log yet' }" | tr -d '\r'
  "${SSH[@]}" "$HOST" "\$p='$RUNS/$NAME/weights/best.pt'; if (Test-Path \$p) { Get-Item \$p | Select-Object FullName,Length,LastWriteTime } else { 'no best.pt yet' }" | tr -d '\r'
  exit 0
fi

say "0) SSH + CUDA + version parity"
"${SSH[@]}" "$HOST" "echo ok" >/dev/null || die "SSH fail: $HOST"
LOCAL_V=$(.venv/bin/python -c "import torch,ultralytics,numpy;print(f'{torch.__version__.split(\"+\")[0]}|{ultralytics.__version__}|{numpy.__version__}')")
# The probe runs detached and writes a file, then we read the file. An inline
# `python -c` over ssh never returns on this box: a child process started inside
# the session holds the exec channel open even after it has printed and exited,
# so the shell keeps waiting for an EOF that never comes. WMI-created processes
# do not belong to the session, so they cannot wedge it.
"${SCP[@]}" scripts/remote_env_probe.py "$HOST:$REMOTE/remote_env_probe.py" \
  || die "scp env probe failed"
"${SSH[@]}" "$HOST" "New-Item -ItemType Directory -Force $REMOTE/logs | Out-Null; Remove-Item -Force $REMOTE/logs/_env_probe.txt -ErrorAction SilentlyContinue; \$r = Invoke-CimMethod -ClassName Win32_Process -MethodName Create -Arguments @{CommandLine='cmd.exe /c \"C:\\fable\\.venv\\Scripts\\python.exe -u C:\\fable\\remote_env_probe.py > C:\\fable\\logs\\_env_probe.txt 2>&1\"'}; Write-Output ('ret=' + \$r.ReturnValue)" >/dev/null \
  || die "remote env probe launch failed"
REMOTE_LINE=""
for _ in $(seq 1 20); do
  REMOTE_LINE=$("${SSH[@]}" "$HOST" "if (Test-Path $REMOTE/logs/_env_probe.txt) { Get-Content $REMOTE/logs/_env_probe.txt -Tail 1 }" | tr -d '\r' | tail -1)
  [[ -n "$REMOTE_LINE" ]] && break
  sleep 5
done
[[ -n "$REMOTE_LINE" ]] || die "remote env probe produced no output"
REMOTE_V="${REMOTE_LINE%|*|*}"
CUDA_OK=$(printf '%s' "$REMOTE_LINE" | awk -F'|' '{print $4}')
DEVICE=$(printf '%s' "$REMOTE_LINE" | awk -F'|' '{print $5}')
echo "  Mac : $LOCAL_V"
echo "  3060: $REMOTE_V"
[[ "$LOCAL_V" == "$REMOTE_V" ]] || die "version mismatch"
[[ "$CUDA_OK" == "True" ]] || die "CUDA not available on remote"
echo "  device: $DEVICE"
[[ -f src/detection/train.py ]] || die "missing repository trainer: src/detection/train.py"

if [[ "$MODE" == "check" ]]; then
  echo "✅ check ok"
  exit 0
fi

[[ -d "$DATASET" ]] || die "missing $DATASET"
[[ -f "$DATASET/data.yaml" ]] || die "missing data.yaml"
[[ -f "$BASE" ]] || die "missing base $BASE"
# sanity: need both pos and neg
n_train=$(find "$DATASET/images/train" -name '*.png' | wc -l | tr -d ' ')
n_val=$(find "$DATASET/images/val" -name '*.png' | wc -l | tr -d ' ')
echo "  dataset train=$n_train val=$n_val"
[[ "$n_train" -gt 1000 ]] || die "train set too small ($n_train)"
[[ "$n_val" -gt 100 ]] || die "val set too small ($n_val)"

say "1) ship dataset + base → 3060"
TAR=$(mktemp -t fable_w20).tar
COPYFILE_DISABLE=1 tar cf "$TAR" --exclude='*.npy' --exclude='*.cache' --exclude='._*' \
  -C "$(dirname "$DATASET")" "$(basename "$DATASET")"
echo "  tar $(du -h "$TAR" | cut -f1)"
"${SCP[@]}" "$TAR" "$HOST:$REMOTE/ds_w20.tar" || die "scp dataset failed"
"${SCP[@]}" "$BASE" "$HOST:$REMOTE/base_w20.pt" || die "scp base failed"
"${SCP[@]}" src/detection/train.py "$HOST:$REMOTE/train_safe.py" \
  || die "scp repository trainer failed"
rm -f "$TAR"
BN=$(basename "$DATASET")
"${SSH[@]}" "$HOST" "cd $REMOTE; New-Item -ItemType Directory -Force datasets,logs,models | Out-Null; Remove-Item -Recurse -Force datasets/$BN -ErrorAction SilentlyContinue; tar xf ds_w20.tar -C datasets; Remove-Item ds_w20.tar; Copy-Item base_w20.pt models/yolo11s_w20.pt -Force" \
  || die "remote extract failed"
"${SSH[@]}" "$HOST" "\$p='$REMOTE/datasets/$BN/data.yaml'; (Get-Content \$p) -replace '^path:.*$', 'path: $REMOTE/datasets/$BN' | Set-Content -Path \$p -Encoding ASCII" \
  || die "remote data.yaml rewrite failed"

say "2) start detached WMI train: $NAME"
# Launch the complete command through WMI.  Do not pipe a multiline body into
# PowerShell's automatic ``$input`` enumerator: Set-Content serializes that
# object as ``PipelineReader...`` instead of writing the command text.
REMOTE_CMD="cmd.exe /c \"cd /d C:\\fable && C:\\fable\\.venv\\Scripts\\python.exe -u C:\\fable\\train_safe.py --name $NAME --model C:/fable/models/yolo11s_w20.pt --data C:/fable/datasets/$BN/data.yaml --epochs $EPOCHS --patience $PATIENCE --batch $BATCH --seed $SEED $FINETUNE_ARG --cache false --workers $WORKERS > C:\\fable\\logs\\$NAME.log 2>&1\""
LAUNCH_OUT=$("${SSH[@]}" "$HOST" "\$r = Invoke-CimMethod -ClassName Win32_Process -MethodName Create -Arguments @{CommandLine='$REMOTE_CMD'}; Write-Output ('pid=' + \$r.ProcessId + ' ret=' + \$r.ReturnValue)")
printf '%s\n' "$LAUNCH_OUT"
[[ "$LAUNCH_OUT" == *"ret=0"* ]] || die "remote WMI launch failed"

echo "  started name=$NAME epochs=$EPOCHS batch=$BATCH seed=$SEED finetune=${FINETUNE_ARG:-auto}"
echo "  watch:  bash scripts/train_w20_midbox_on_3060.sh --status --host $HOST --name $NAME"
echo "  log:    ssh $HOST \"Get-Content C:\\\\fable\\\\logs\\\\$NAME.log -Tail 40\""
echo "  fetch later:"
echo "    scp $HOST:$RUNS/$NAME/weights/best.pt analysis/output/lsv2_stageb/$NAME/weights/best.pt"
