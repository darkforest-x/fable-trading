#!/usr/bin/env bash
# Stage, start, observe and collect the frozen 15m L2 candidate scan on the LAN
# RTX 3060.  The worker receives immutable code, model, manifest and OHLCV
# snapshot bytes, then returns candidate ledgers only.  It never builds labels,
# trains L2, reads holdout, promotes a model, writes forward state or trades.
set -euo pipefail

cd "$(dirname "$0")/.."

EXPERIMENT_ID="exp-15m-ma-launch-l2-global-context-v1"
OUT="analysis/output/ma_launch_l2_global_context_v1"
RESULTS="experiments/active/$EXPERIMENT_ID/results"
PREREG="experiments/active/$EXPERIMENT_ID/preregistration.json"
HOST="${FABLE_3060_HOST:-Administrator@192.168.1.5}"
REMOTE="C:/fable/l2_exp-15m-ma-launch-global-context-v1"
REMOTE_SFTP="/C:/fable/l2_exp-15m-ma-launch-global-context-v1"
PYTHON="${PYTHON:-.venv/bin/python}"
BATCH_SIZE=32
MODE="run"

SSH_PS=(bash scripts/ssh_ps.sh)
SCP=(scp -o BatchMode=yes -o ConnectTimeout=15 -q)

die() { printf '[x] %s\n' "$*" >&2; exit 1; }
say() { printf '\n=== %s ===\n' "$*"; }

usage() {
  cat <<'EOF'
Usage:
  bash scripts/run_15m_ma_launch_l2_global_context_on_3060.sh [--check|--stage|--start|--status|--collect] [--host user@ip] [--batch-size N]

Default runs check -> stage -> detached start.  Collection fails closed until
both remote scan.exit=0 and the atomic terminal scan_receipt.json exist.  Only
candidate ledgers and scan logs return; market snapshots and weights do not.
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
[[ -f "$PREREG" ]] || die "missing preregistration: $PREREG"
[[ -f "$RESULTS/snapshot_receipt.json" ]] || die "missing frozen snapshot receipt"
[[ -d "$OUT/snapshot" ]] || die "missing frozen snapshot directory"

source_commit() {
  git branch --show-current | grep -qx main || die "official inference must launch from main"
  git status --short -- \
    scripts/research_15m_ma_launch_l2_global_context.py \
    scripts/run_15m_ma_launch_l2_global_context_on_3060.sh \
    scripts/windows/run_l2_global_context_scan.ps1 \
    yoyo/layers/l1_detection/render.py \
    yoyo/layers/l2_judgment/features.py \
    yoyo/layers/l2_judgment/labeling.py \
    yoyo/layers/l2_judgment/train.py \
    "$PREREG" \
    | grep -q . && die "L2 scan contract has uncommitted changes"
  git rev-parse HEAD
}

check() {
  say "3060 CUDA and inference version contract"
  local local_v remote_v remote_contract cuda_ok
  local_v=$("$PYTHON" -c "import torch,ultralytics,numpy;print('|'.join((torch.__version__.split('+')[0],ultralytics.__version__,numpy.__version__)))")
  remote_v=$(FABLE_SSH_DEADLINE=45 "${SSH_PS[@]}" "$HOST" "& 'C:/fable/.venv/Scripts/python.exe' -c \"import torch,ultralytics,numpy; print('|'.join((torch.__version__.split('+')[0], ultralytics.__version__, numpy.__version__, str(torch.cuda.is_available()), torch.cuda.get_device_name(0) if torch.cuda.is_available() else 'NO_CUDA')))\"" | "$PYTHON" -c "import sys; rows=sys.stdin.read().replace('\\ufeff','').strip().splitlines(); print(rows[-1] if rows else '')")
  remote_contract=$(printf '%s' "$remote_v" | awk -F'|' '{print $1 "|" $2 "|" $3}')
  cuda_ok=$(printf '%s' "$remote_v" | awk -F'|' '{print $4}')
  printf 'Mac:  %s\n3060: %s\n' "$local_v" "$remote_v"
  [[ "$local_v" == "$remote_contract" ]] || die "torch/ultralytics/numpy mismatch"
  [[ "$cuda_ok" == "True" ]] || die "3060 CUDA unavailable"
}

stage() {
  say "stage immutable offline scan bundle"
  local commit bundle
  commit=$(source_commit)
  bundle=$(mktemp -t fable_l2_global_context).tar
  COPYFILE_DISABLE=1 tar cf "$bundle" --exclude='._*' --exclude='*.pyc' --exclude='__pycache__' \
    scripts/research_15m_ma_launch_l2_global_context.py \
    scripts/windows/run_l2_global_context_scan.ps1 \
    yoyo \
    "$PREREG" "$RESULTS/snapshot_receipt.json" "$OUT/snapshot" \
    experiments/active/exp-15m-ma-launch-model-compare-all3d-20260831-v1/preregistration.json \
    experiments/active/exp-15m-ma-launch-model-compare-all3d-20260831-v1/results/comparison_summary.json \
    experiments/active/exp-15m-ma-launch-model-compare-all3d-20260831-v1/results/model_summary.csv \
    analysis/output/ma_launch_owner_grade_a8000_neg24000_v1/ma_launch_owner_grade_a8000_neg24000_v1_y11s_ft1280_full40/weights/best.pt \
    datasets/ma_launch_owner_grade_a8000_yolo_neg24000_v1/manifest.jsonl
  printf 'bundle: %s (%s)\n' "$bundle" "$(du -h "$bundle" | awk '{print $1}')"
  FABLE_SSH_DEADLINE=60 "${SSH_PS[@]}" "$HOST" "\$root='$REMOTE'; if (Test-Path -LiteralPath \$root) { throw 'remote bundle directory already exists: ' + \$root }; New-Item -ItemType Directory -Force \$root | Out-Null; Write-Output ('created=' + \$root)"
  "${SCP[@]}" "$bundle" "$HOST:$REMOTE_SFTP/payload.tar"
  FABLE_SSH_DEADLINE=120 "${SSH_PS[@]}" "$HOST" "\$root='$REMOTE'; & tar -xf (Join-Path \$root 'payload.tar') -C \$root; if (\$LASTEXITCODE -ne 0) { throw 'tar extraction failed' }; if (-not (Test-Path -LiteralPath (Join-Path \$root 'scripts/research_15m_ma_launch_l2_global_context.py'))) { throw 'scanner missing after extraction' }; Write-Output ('staged=' + \$root + ' source_commit=$commit')"
}

start() {
  say "start detached frozen scan"
  local commit
  commit=$(source_commit)
  FABLE_SSH_DEADLINE=60 "${SSH_PS[@]}" "$HOST" "\$root='$REMOTE'; if (-not (Test-Path -LiteralPath \$root)) { throw 'remote bundle is not staged' }; if (Test-Path -LiteralPath (Join-Path \$root 'scan.exit')) { throw 'refusing to overwrite existing scan.exit' }; \$runner=Join-Path \$root 'scripts/windows/run_l2_global_context_scan.ps1'; \$stdout=Join-Path \$root 'scan.stdout.log'; \$stderr=Join-Path \$root 'scan.stderr.log'; \$command=\"cmd.exe /c powershell.exe -NoProfile -NonInteractive -ExecutionPolicy Bypass -File \$runner -Root \$root -SourceCommit $commit -BatchSize $BATCH_SIZE > \$stdout 2> \$stderr\"; \$created=Invoke-CimMethod -ClassName Win32_Process -MethodName Create -Arguments @{CommandLine=\$command}; if (\$created.ReturnValue -ne 0) { throw ('WMI create failed ret=' + \$created.ReturnValue) }; [System.IO.File]::WriteAllText((Join-Path \$root 'scan.pid'), (\$created.ProcessId.ToString() + [Environment]::NewLine), [System.Text.Encoding]::ASCII); [System.IO.File]::WriteAllText((Join-Path \$root 'scan.source_commit'), ('$commit' + [Environment]::NewLine), [System.Text.Encoding]::ASCII); Write-Output ('pid=' + \$created.ProcessId + ' source_commit=$commit batch=$BATCH_SIZE')"
}

status() {
  say "remote frozen scan status"
  FABLE_SSH_DEADLINE=45 "${SSH_PS[@]}" "$HOST" "\$root='$REMOTE'; if (-not (Test-Path -LiteralPath \$root)) { throw 'remote bundle is not staged' }; \$exit=Join-Path \$root 'scan.exit'; \$pidPath=Join-Path \$root 'scan.pid'; \$identityPath=Join-Path \$root 'scan.source_commit'; \$scanDir=Join-Path \$root 'analysis/output/ma_launch_l2_global_context_v1/scan_by_symbol'; \$workers=@(Get-CimInstance Win32_Process | Where-Object { \$_.Name -like 'python*' -and \$_.CommandLine -and \$_.CommandLine -like '*research_15m_ma_launch_l2_global_context.py*' }); if (Test-Path \$identityPath) { \$identity=(Get-Content \$identityPath -Raw).Trim(); \$workers=@(\$workers | Where-Object { \$_.CommandLine -like ('*' + \$identity + '*') }); Write-Output ('workload_identity=source_commit:' + \$identity) } else { Write-Output 'workload_identity=fallback_script_only' }; Write-Output ('symbol_receipts=' + @(Get-ChildItem \$scanDir -Filter 'receipt.json' -Recurse -File -ErrorAction SilentlyContinue).Count); Write-Output ('workload_running=' + [bool]\$workers.Count + ' worker_pids=' + ((\$workers | ForEach-Object { \$_.ProcessId }) -join ',')); if (Test-Path \$exit) { Write-Output ('scan.exit=' + (Get-Content \$exit -Raw).Trim()) } elseif (Test-Path \$pidPath) { Write-Output ('launcher_pid_record=' + (Get-Content \$pidPath -Raw).Trim()) } else { Write-Output 'launcher_pid_record=missing' }; foreach (\$name in @('scan.stdout.log','scan.stderr.log')) { \$path=Join-Path \$root \$name; Write-Output ('--- ' + \$name + ' ---'); if (Test-Path \$path) { Get-Content \$path -Tail 30 } else { Write-Output 'missing' } }"
}

verify_collected() {
  "$PYTHON" - <<'PY'
import hashlib
import json
from pathlib import Path

root = Path.cwd()
out = root / "analysis/output/ma_launch_l2_global_context_v1"
results = root / "experiments/active/exp-15m-ma-launch-l2-global-context-v1/results"
receipt = json.loads((results / "scan_receipt.json").read_text(encoding="utf-8"))

def sha(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()

assert receipt["symbols"] == 54, receipt["symbols"]
assert len(receipt["per_symbol"]) == 54, len(receipt["per_symbol"])
assert receipt["holdout_rows_read"] == 0
assert receipt["network_reads"] == 0
assert receipt["training_or_tuning"] is False
assert receipt["promoted"] is False and receipt["deployed"] is False
assert receipt["forward_state_changed"] is False and receipt["orders_placed"] is False
for field in ("accepted_candidates", "episodes"):
    path = root / receipt[f"{field}_path"]
    assert path.is_file(), path
    assert sha(path) == receipt[f"{field}_sha256"], field
for item in receipt["per_symbol"]:
    symbol_dir = out / "scan_by_symbol" / item["symbol"]
    assert sha(symbol_dir / "accepted_candidates.csv") == item["candidates_sha256"]
    assert sha(symbol_dir / "episodes.csv") == item["episodes_sha256"]
print(f"verified symbols=54 candidates={receipt['raw_accepted_candidates']} episodes={receipt['overlap_episodes']} holdout=0")
PY
}

collect() {
  say "collect terminal candidate ledgers only"
  local exit_code archive collect_tmp
  [[ ! -e "$RESULTS/scan_receipt.json" ]] || die "refusing to overwrite local terminal scan receipt"
  [[ ! -e "$OUT/scan_by_symbol" ]] || die "refusing to merge into an existing local per-symbol scan"
  exit_code=$(FABLE_SSH_DEADLINE=45 "${SSH_PS[@]}" "$HOST" "\$root='$REMOTE'; \$exit=Join-Path \$root 'scan.exit'; \$receipt=Join-Path \$root 'experiments/active/$EXPERIMENT_ID/results/scan_receipt.json'; if (-not (Test-Path \$exit)) { throw 'scan has not written scan.exit' }; if (-not (Test-Path \$receipt -PathType Leaf)) { throw 'terminal scan receipt is missing' }; Get-Content \$exit -Raw" | "$PYTHON" -c "import sys; print(sys.stdin.read().replace('\\ufeff','').strip())")
  [[ "$exit_code" == "0" ]] || die "remote scan did not succeed: scan.exit=$exit_code"
  FABLE_SSH_DEADLINE=120 "${SSH_PS[@]}" "$HOST" "\$root='$REMOTE'; \$archive=Join-Path \$root 'scan_results.tar'; & tar -cf \$archive -C \$root 'analysis/output/ma_launch_l2_global_context_v1/scan_by_symbol' 'analysis/output/ma_launch_l2_global_context_v1/accepted_candidates.csv' 'analysis/output/ma_launch_l2_global_context_v1/episodes.csv' 'experiments/active/$EXPERIMENT_ID/results/scan_receipt.json' 'scan.stdout.log' 'scan.stderr.log' 'scan.exit'; if (\$LASTEXITCODE -ne 0) { throw 'could not build result archive' }; Get-Item \$archive | Select-Object FullName,Length | Format-List | Out-String"
  archive=$(mktemp -t fable_l2_scan_results).tar
  collect_tmp=$(mktemp -d -t fable_l2_scan_collect)
  [[ -d "$collect_tmp" && "$(basename "$collect_tmp")" == fable_l2_scan_collect.* ]] \
    || die "unsafe collection temp directory: $collect_tmp"
  trap '[[ -d "$collect_tmp" && "$(basename "$collect_tmp")" == fable_l2_scan_collect.* ]] && rm -rf -- "$collect_tmp"' RETURN
  "${SCP[@]}" "$HOST:$REMOTE_SFTP/scan_results.tar" "$archive"
  tar -tf "$archive" | "$PYTHON" -c "import sys; allowed=('analysis/output/ma_launch_l2_global_context_v1/scan_by_symbol/', 'analysis/output/ma_launch_l2_global_context_v1/accepted_candidates.csv', 'analysis/output/ma_launch_l2_global_context_v1/episodes.csv', 'experiments/active/$EXPERIMENT_ID/results/scan_receipt.json', 'scan.stdout.log', 'scan.stderr.log', 'scan.exit'); rows=[x.strip() for x in sys.stdin if x.strip()]; bad=[x for x in rows if not any(x==p or x.startswith(p) for p in allowed)]; assert rows and not bad, bad"
  tar -xf "$archive" -C "$collect_tmp"
  mkdir -p "$OUT" "$RESULTS"
  cp -R "$collect_tmp/$OUT/scan_by_symbol" "$OUT/"
  cp "$collect_tmp/$OUT/accepted_candidates.csv" "$OUT/accepted_candidates.csv"
  cp "$collect_tmp/$OUT/episodes.csv" "$OUT/episodes.csv"
  cp "$collect_tmp/$RESULTS/scan_receipt.json" "$RESULTS/scan_receipt.json"
  cp "$collect_tmp/scan.stdout.log" "$RESULTS/remote_scan.stdout.log"
  cp "$collect_tmp/scan.stderr.log" "$RESULTS/remote_scan.stderr.log"
  cp "$collect_tmp/scan.exit" "$RESULTS/remote_scan.exit"
  verify_collected
  printf 'collected and verified: %s\n' "$RESULTS/scan_receipt.json"
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
