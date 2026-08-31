#!/usr/bin/env bash
# Stage and run the frozen five-model all-universe inference bundle on the LAN
# RTX 3060.  The Mac remains the only durable project state: it creates the
# one market snapshot, verifies all hashes, renders review documents, and owns
# the final report.  This script sends a disposable offline bundle to the GPU
# worker and retrieves only the resulting candidate ledgers.
set -euo pipefail

cd "$(dirname "$0")/.."

EXPERIMENT_ID="exp-15m-ma-launch-model-compare-all3d-20260831-v1"
OUT="analysis/output/ma_launch_model_compare_all3d_20260831_v1"
RESULTS="experiments/active/$EXPERIMENT_ID/results"
HOST="${FABLE_3060_HOST:-Administrator@192.168.1.5}"
REMOTE="C:/fable/model_compare_$EXPERIMENT_ID"
REMOTE_SFTP="/C:/fable/model_compare_$EXPERIMENT_ID"
PYTHON="${PYTHON:-.venv/bin/python}"
BATCH_SIZE=16
MODE="run"

SSH_PS=(bash scripts/ssh_ps.sh)
SCP=(scp -o BatchMode=yes -o ConnectTimeout=15 -q)

die() { printf '[x] %s\n' "$*" >&2; exit 1; }
say() { printf '\n=== %s ===\n' "$*"; }

usage() {
  cat <<'EOF'
Usage:
  bash scripts/run_15m_ma_launch_model_compare_all3d_on_3060.sh [--check|--stage|--start|--status|--collect] [--host user@ip] [--batch-size N]

Default (no mode) runs check -> stage -> detached start.  --collect is allowed
only after the remote scanner wrote scan.exit=0; it brings back candidate
ledgers, never market data or weights.
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --check) MODE="check"; shift ;;
    --stage) MODE="stage"; shift ;;
    --start) MODE="start"; shift ;;
    --status) MODE="status"; shift ;;
    --collect) MODE="collect"; shift ;;
    --host) HOST="$2"; shift 2 ;;
    --batch-size) BATCH_SIZE="$2"; shift 2 ;;
    -h|--help) usage; exit 0 ;;
    *) die "unknown argument: $1" ;;
  esac
done

[[ "$BATCH_SIZE" =~ ^[1-9][0-9]*$ ]] || die "--batch-size must be a positive integer"
[[ -f "$RESULTS/fetch_receipt.json" ]] || die "missing frozen fetch receipt: $RESULTS/fetch_receipt.json"
[[ -d "$OUT/kline_snapshot" ]] || die "missing frozen source snapshot: $OUT/kline_snapshot"

source_commit() {
  git branch --show-current | grep -qx main || die "official inference must be launched from main"
  git status --short -- \
    scripts/scan_15m_ma_launch_model_compare_all3d.py \
    scripts/scan_15m_ma_launch_t3_daily_movers.py \
    scripts/run_15m_ma_launch_model_compare_all3d_on_3060.sh \
    scripts/windows/run_model_compare_all3d_scan.ps1 \
    "experiments/active/$EXPERIMENT_ID/preregistration.json" \
    | grep -q . && die "scanner contract has uncommitted changes"
  git rev-parse HEAD
}

check() {
  say "3060 environment and version contract"
  local local_v remote_v
  local_v=$("$PYTHON" -c "import torch,ultralytics,numpy;print(f'{torch.__version__.split(chr(43))[0]}|{ultralytics.__version__}|{numpy.__version__}')")
  remote_v=$(FABLE_SSH_DEADLINE=45 "${SSH_PS[@]}" "$HOST" "& 'C:/fable/.venv/Scripts/python.exe' -c \"import torch,ultralytics,numpy; print('|'.join((torch.__version__.split('+')[0], ultralytics.__version__, numpy.__version__, str(torch.cuda.is_available()), torch.cuda.get_device_name(0) if torch.cuda.is_available() else 'NO_CUDA')))\"" | "$PYTHON" -c "import sys; rows=sys.stdin.read().replace('\\ufeff','').strip().splitlines(); print(rows[-1] if rows else '')")
  local remote_contract
  remote_contract=$(printf '%s' "$remote_v" | awk -F'|' '{print $1 "|" $2 "|" $3}')
  local cuda_ok
  cuda_ok=$(printf '%s' "$remote_v" | awk -F'|' '{print $4}')
  printf 'Mac:  %s\n3060: %s\n' "$local_v" "$remote_v"
  [[ "$local_v" == "$remote_contract" ]] || die "torch/ultralytics/numpy mismatch"
  [[ "$cuda_ok" == "True" ]] || die "3060 CUDA unavailable"
}

stage() {
  say "package frozen source snapshot and five immutable weights"
  local commit bundle
  commit=$(source_commit)
  bundle=$(mktemp -t fable_model_compare_all3d).tar
  COPYFILE_DISABLE=1 tar cf "$bundle" --exclude='._*' --exclude='*.pyc' --exclude='__pycache__' \
    scripts/scan_15m_ma_launch_model_compare_all3d.py \
    scripts/scan_15m_ma_launch_t3_daily_movers.py \
    scripts/windows/run_model_compare_all3d_scan.ps1 \
    yoyo/__init__.py yoyo/layers/__init__.py yoyo/layers/l1_detection/__init__.py \
    yoyo/layers/l1_detection/data.py yoyo/layers/l1_detection/render.py \
    "experiments/active/$EXPERIMENT_ID/preregistration.json" \
    "$OUT" "$RESULTS/fetch_receipt.json" \
    analysis/output/ma_launch_t3_10000_v1/ma_launch_t3_10000_v1_y11s_ft/weights/best.pt \
    analysis/output/ma_launch_t3_10000_v1/ma_launch_t3_10000_v1_y11s_ft_imgsz1280/weights/best.pt \
    analysis/output/ma_launch_owner_yolo_neg30000_v2/ma_launch_owner_yolo_neg30000_v2_y11s_ft960/weights/best.pt \
    analysis/output/ma_launch_owner_grade_a8000_neg24000_v1/ma_launch_owner_grade_a8000_neg24000_v1_y11s_ft960/weights/best.pt \
    analysis/output/ma_launch_owner_grade_a8000_neg24000_v1/ma_launch_owner_grade_a8000_neg24000_v1_y11s_ft1280_full40/weights/best.pt \
    datasets/ma_launch_t3_10000_v1/manifest.jsonl \
    datasets/ma_launch_owner_autofill10000_yolo_neg30000_v2/manifest.jsonl \
    datasets/ma_launch_owner_grade_a8000_yolo_neg24000_v1/manifest.jsonl
  printf 'bundle: %s (%s)\n' "$bundle" "$(du -h "$bundle" | awk '{print $1}')"
  FABLE_SSH_DEADLINE=60 "${SSH_PS[@]}" "$HOST" "\$root='$REMOTE'; if (Test-Path -LiteralPath \$root) { throw 'remote bundle directory already exists: ' + \$root }; New-Item -ItemType Directory -Force \$root | Out-Null; Write-Output ('created=' + \$root)"
  "${SCP[@]}" "$bundle" "$HOST:$REMOTE_SFTP/payload.tar"
  FABLE_SSH_DEADLINE=90 "${SSH_PS[@]}" "$HOST" "\$root='$REMOTE'; & tar -xf (Join-Path \$root 'payload.tar') -C \$root; if (\$LASTEXITCODE -ne 0) { throw 'tar extraction failed' }; if (-not (Test-Path -LiteralPath (Join-Path \$root 'scripts/scan_15m_ma_launch_model_compare_all3d.py'))) { throw 'scanner missing after extraction' }; Write-Output ('staged=' + \$root + ' source_commit=$commit')"
}

start() {
  say "start detached offline inference on RTX 3060"
  local commit
  commit=$(source_commit)
  FABLE_SSH_DEADLINE=60 "${SSH_PS[@]}" "$HOST" "\$root='$REMOTE'; if (-not (Test-Path -LiteralPath \$root)) { throw 'remote bundle is not staged' }; if (Test-Path -LiteralPath (Join-Path \$root 'scan.exit')) { throw 'refusing to overwrite existing scan.exit' }; \$ps = 'powershell.exe'; \$args = @('-NoProfile','-NonInteractive','-ExecutionPolicy','Bypass','-File',(Join-Path \$root 'scripts/windows/run_model_compare_all3d_scan.ps1'),'-Root',\$root,'-SourceCommit','$commit','-BatchSize','$BATCH_SIZE'); \$p = Start-Process -FilePath \$ps -ArgumentList \$args -WorkingDirectory \$root -RedirectStandardOutput (Join-Path \$root 'scan.stdout.log') -RedirectStandardError (Join-Path \$root 'scan.stderr.log') -PassThru; [System.IO.File]::WriteAllText((Join-Path \$root 'scan.pid'), (\$p.Id.ToString() + [Environment]::NewLine), [System.Text.Encoding]::ASCII); Write-Output ('pid=' + \$p.Id + ' source_commit=$commit batch=$BATCH_SIZE')"
}

status() {
  say "remote scan status"
  FABLE_SSH_DEADLINE=45 "${SSH_PS[@]}" "$HOST" "\$root='$REMOTE'; if (-not (Test-Path -LiteralPath \$root)) { throw 'remote bundle is not staged' }; \$exit = Join-Path \$root 'scan.exit'; \$pidPath = Join-Path \$root 'scan.pid'; if (Test-Path \$exit) { Write-Output ('scan.exit=' + (Get-Content \$exit -Raw).Trim()) } elseif (Test-Path \$pidPath) { \$pid = [int](Get-Content \$pidPath -Raw); \$p = Get-Process -Id \$pid -ErrorAction SilentlyContinue; Write-Output ('scan.pid=' + \$pid + ' running=' + [bool]\$p) } else { Write-Output 'not started' }; foreach (\$name in @('scan.stdout.log','scan.stderr.log')) { \$p = Join-Path \$root \$name; Write-Output ('--- ' + \$name + ' ---'); if (Test-Path \$p) { Get-Content \$p -Tail 30 } else { Write-Output 'missing' } }"
}

collect() {
  say "retrieve only offline inference ledgers"
  local archive
  local exit_code
  exit_code=$(FABLE_SSH_DEADLINE=45 "${SSH_PS[@]}" "$HOST" "\$root='$REMOTE'; \$exit = Join-Path \$root 'scan.exit'; if (-not (Test-Path \$exit)) { throw 'scan has not written scan.exit' }; Get-Content \$exit -Raw" | "$PYTHON" -c "import sys; print(sys.stdin.read().replace('\\ufeff','').strip())")
  [[ "$exit_code" == "0" ]] || die "remote scan did not succeed: scan.exit=$exit_code"
  FABLE_SSH_DEADLINE=90 "${SSH_PS[@]}" "$HOST" "\$root='$REMOTE'; \$archive=Join-Path \$root 'scan_results.tar'; & tar -cf \$archive -C \$root 'analysis/output/ma_launch_model_compare_all3d_20260831_v1/models' 'experiments/active/$EXPERIMENT_ID/results/models' 'experiments/active/$EXPERIMENT_ID/results/scan_receipt.json' 'scan.stdout.log' 'scan.stderr.log' 'scan.exit'; if (\$LASTEXITCODE -ne 0) { throw 'could not build result archive' }; Get-Item \$archive | Select-Object FullName,Length | Format-List | Out-String"
  archive=$(mktemp -t fable_model_compare_results).tar
  "${SCP[@]}" "$HOST:$REMOTE_SFTP/scan_results.tar" "$archive"
  tar -tf "$archive" | "$PYTHON" -c "import sys; allowed=('analysis/output/ma_launch_model_compare_all3d_20260831_v1/models/', 'experiments/active/$EXPERIMENT_ID/results/models/', 'experiments/active/$EXPERIMENT_ID/results/scan_receipt.json', 'scan.stdout.log', 'scan.stderr.log', 'scan.exit'); bad=[x.strip() for x in sys.stdin if x.strip() and not any(x.strip()==p or x.strip().startswith(p) for p in allowed)]; assert not bad, bad"
  tar -xf "$archive" -C .
  printf 'collected archive: %s\n' "$archive"
}

case "$MODE" in
  check) check ;;
  stage) check; stage ;;
  start) check; start ;;
  status) status ;;
  collect) collect ;;
  run) check; stage; start ;;
  *) die "unknown mode: $MODE" ;;
esac
