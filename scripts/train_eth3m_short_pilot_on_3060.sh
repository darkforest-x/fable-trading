#!/bin/bash
# Ship and start the native ETH 3m short-detector pilot on the Windows RTX 3060.
#
# This launcher deliberately has a narrow responsibility boundary:
#   - it checks version/CUDA parity, ships immutable training inputs, and starts
#     one detached WMI job;
#   - it can inspect that job, its Windows-side log, and any matching best.pt;
#   - it never waits for training to finish, downloads artifacts, runs any
#     frozen/holdout evaluation, promotes a model, or writes models/ACTIVE.
#
# WMI starts a CRLF .cmd file rather than keeping Python attached to SSH.  An
# earlier foreground SSH training process lost stdout while torch.save was
# writing and corrupted its checkpoint.  The optional --wait-pid is implemented
# inside that detached .cmd, so it can queue behind an existing GPU scan without
# keeping this Mac-side launcher alive.
#
# Usage:
#   bash scripts/train_eth3m_short_pilot_on_3060.sh --check
#   bash scripts/train_eth3m_short_pilot_on_3060.sh --status
#   bash scripts/train_eth3m_short_pilot_on_3060.sh --wait-pid 12345
#   bash scripts/train_eth3m_short_pilot_on_3060.sh --name trial_b --epochs 80
set -euo pipefail

cd "$(dirname "$0")/.."

HOST="${FABLE_3060_HOST:-}"
REMOTE="C:/fable"
REMOTE_PY="$REMOTE/.venv/Scripts/python.exe"
LOCAL_PY=".venv/bin/python"

DATASET="datasets/eth_3m_short_pilot_v1"
BASE="models/yolo11n.pt"
NAME="eth3m_short_pilot_v1_cold"
EPOCHS=100
PATIENCE=20
WAIT_PID=""
MODE="run"

SSH=(ssh -o BatchMode=yes -o ConnectTimeout=15)
SCP=(scp -o BatchMode=yes -o ConnectTimeout=15 -q)

TMP_TAR=""
TMP_CMD=""
cleanup() {
  # Both names come only from mktemp.  Keep cleanup explicit: never glob and
  # never derive a deletion target from a user-provided dataset/name argument.
  set +e
  if [[ -n "$TMP_TAR" && -f "$TMP_TAR" ]]; then
    rm -f -- "$TMP_TAR"
  fi
  if [[ -n "$TMP_CMD" && -f "$TMP_CMD" ]]; then
    rm -f -- "$TMP_CMD"
  fi
}
trap cleanup EXIT

say() { printf '\n\033[1;36m=== %s ===\033[0m\n' "$*"; }
die() { printf '\033[1;31m[X] %s\033[0m\n' "$*" >&2; exit 1; }

usage() {
  cat <<'EOF'
Usage: bash scripts/train_eth3m_short_pilot_on_3060.sh [options]

Required environment:
  FABLE_3060_HOST   Current 3060 SSH target as user@ip; no DHCP address is guessed

Modes (mutually exclusive):
  --check             Check SSH, torch/ultralytics/numpy parity, and CUDA only
  --status            Show matching task processes, log tail, and matching best.pt

Training options:
  --name NAME         Run name (default: eth3m_short_pilot_v1_cold)
  --dataset PATH      Local dataset (default: datasets/eth_3m_short_pilot_v1)
  --base PATH         Local cold-start weights (default: models/yolo11n.pt)
  --epochs N          Epoch limit (default: 100)
  --patience N        Early-stop patience (default: 20)
  --wait-pid PID      In the detached Windows job, wait for PID to exit first
  -h, --help          Show this help
EOF
}

set_mode() {
  local requested="$1"
  if [[ "$MODE" != "run" && "$MODE" != "$requested" ]]; then
    die "--check and --status are mutually exclusive"
  fi
  MODE="$requested"
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --check)
      set_mode "check"
      shift
      ;;
    --status)
      set_mode "status"
      shift
      ;;
    --name|--dataset|--base|--epochs|--patience|--wait-pid)
      [[ $# -ge 2 ]] || die "$1 requires a value"
      case "$1" in
        --name) NAME="$2" ;;
        --dataset) DATASET="$2" ;;
        --base) BASE="$2" ;;
        --epochs) EPOCHS="$2" ;;
        --patience) PATIENCE="$2" ;;
        --wait-pid) WAIT_PID="$2" ;;
      esac
      shift 2
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      die "unknown argument: $1"
      ;;
  esac
done

[[ -n "$HOST" ]] || die \
  "FABLE_3060_HOST is required; the 3060 DHCP address must never be guessed"

# These values are embedded in a Windows path/command.  Restrict them to plain
# tokens rather than attempting to quote arbitrary cmd.exe metacharacters.
[[ "$NAME" =~ ^[A-Za-z0-9][A-Za-z0-9._-]*$ ]] \
  || die "unsafe --name (allowed: letters, digits, dot, underscore, dash): $NAME"
[[ "$EPOCHS" =~ ^[1-9][0-9]*$ ]] || die "--epochs must be a positive integer"
[[ "$PATIENCE" =~ ^[0-9]+$ ]] || die "--patience must be a non-negative integer"
if [[ -n "$WAIT_PID" ]]; then
  [[ "$WAIT_PID" =~ ^[1-9][0-9]*$ ]] || die "--wait-pid must be a positive integer"
  [[ "$MODE" == "run" ]] || die "--wait-pid is only valid in training mode"
fi

# Send a PowerShell program on stdin.  This avoids three nested quoting layers
# (bash -> Windows OpenSSH default shell -> PowerShell), especially for WMI and
# regex status queries.  PowerShell accepts LF on stdin; the uploaded batch file
# itself is emitted with CRLF below because cmd.exe is stricter.
remote_ps() {
  "${SSH[@]}" "$HOST" \
    "powershell.exe -NoLogo -NoProfile -NonInteractive -Command -"
}

check_remote() {
  local local_v remote_v remote_cuda cuda_ok gpu_name gpu_mem version_ps cuda_ps

  say "3060 connectivity + reproducibility check"
  [[ -x "$LOCAL_PY" ]] || die "local Python missing or not executable: $LOCAL_PY"

  if ! remote_ps >/dev/null <<'PS'; then
$ErrorActionPreference = 'Stop'
if (-not (Test-Path -LiteralPath 'C:/fable')) { throw 'missing C:/fable' }
Write-Output 'ssh-ok'
PS
    die "SSH/PowerShell unavailable: $HOST"
  fi

  if ! local_v="$($LOCAL_PY -c 'import torch,ultralytics,numpy; print(torch.__version__.split("+")[0]+"|"+ultralytics.__version__+"|"+numpy.__version__)' 2>/dev/null)"; then
    die "cannot import local torch, ultralytics, and numpy with $LOCAL_PY"
  fi

  # Windows PowerShell 5 strips embedded double quotes when forwarding native
  # command arguments read through `-Command -`.  Use chr(124) as sep so the
  # Python snippet contains no nested quote that PowerShell can consume.
  version_ps="
& '$REMOTE_PY' -c 'import torch,ultralytics,numpy;print(torch.__version__.split(chr(43))[0],ultralytics.__version__,numpy.__version__,sep=chr(124))'
if (\$LASTEXITCODE -ne 0) { exit \$LASTEXITCODE }
"
  if ! remote_v="$(remote_ps <<<"$version_ps" 2>/dev/null | tr -d '\r\n')"; then
    die "cannot import remote torch, ultralytics, and numpy with $REMOTE_PY"
  fi

  printf '  Mac : %s\n' "$local_v"
  printf '  3060: %s\n' "$remote_v"
  [[ "$local_v" == "$remote_v" ]] \
    || die "version mismatch; align torch/ultralytics/numpy before training"

  cuda_ps="
& '$REMOTE_PY' -c 'import torch;ok=torch.cuda.is_available();print(ok,torch.cuda.get_device_name(0),round(torch.cuda.get_device_properties(0).total_memory/1024**3,1),sep=chr(124))'
if (\$LASTEXITCODE -ne 0) { exit \$LASTEXITCODE }
"
  if ! remote_cuda="$(remote_ps <<<"$cuda_ps" 2>/dev/null | tr -d '\r\n')"; then
    die "remote CUDA probe failed"
  fi
  IFS='|' read -r cuda_ok gpu_name gpu_mem <<<"$remote_cuda"
  [[ "$cuda_ok" == "True" ]] || die "remote CUDA unavailable: $remote_cuda"
  printf '  CUDA: %s (%s GB)\n' "$gpu_name" "$gpu_mem"
}

show_status() {
  local status_ps
  status_ps="
\$ErrorActionPreference = 'Stop'
\$name = '$NAME'
\$namePattern = '(?:^|\s)--name\s+' + [regex]::Escape(\$name) + '(?:\s|\$)'

Write-Output ('=== matching processes: ' + \$name + ' ===')
\$processes = @(Get-CimInstance Win32_Process | Where-Object {
  \$_.CommandLine -and (
    (\$_.CommandLine -like '*train_eth3m_short_pilot.py*' -and
      \$_.CommandLine -match \$namePattern) -or
    \$_.CommandLine -like ('*launch_eth3m_short_pilot_' + \$name + '.cmd*')
  )
})
if (\$processes.Count -eq 0) {
  Write-Output '(none)'
} else {
  \$processes |
    Select-Object ProcessId, ParentProcessId, CreationDate, Name, CommandLine |
    Format-List | Out-String -Width 4096 | Write-Output
}

\$log = 'C:/fable/logs/$NAME.log'
Write-Output ('=== tail: ' + \$log + ' ===')
if (Test-Path -LiteralPath \$log) {
  Get-Content -LiteralPath \$log -Tail 50
} else {
  Write-Output '(missing)'
}

Write-Output '=== matching best.pt ==='
\$best = @(
  'C:/fable/runs/detect/' + \$name + '/weights/best.pt',
  'C:/fable/runs/detect/runs/detect/' + \$name + '/weights/best.pt'
) | Where-Object { Test-Path -LiteralPath \$_ } | ForEach-Object { Get-Item -LiteralPath \$_ }
if (\$best.Count -eq 0) {
  Write-Output '(none)'
} else {
  \$best | Select-Object FullName, Length, LastWriteTime |
    Format-Table -AutoSize | Out-String -Width 4096 | Write-Output
}
"
  remote_ps <<<"$status_ps"
}

if [[ "$MODE" == "status" ]]; then
  show_status
  exit 0
fi

check_remote
if [[ "$MODE" == "check" ]]; then
  printf '\nCheck passed; no files were synced and no training was started.\n'
  exit 0
fi

# Normal mode validates only development training artifacts.  It intentionally
# has no reference to any holdout dataset, frozen evaluator, promotion script,
# forward log, or ACTIVE pointer.
DATASET="${DATASET%/}"
[[ -n "$DATASET" && -d "$DATASET" ]] || die "dataset directory missing: $DATASET"
[[ -s "$DATASET/build_meta.json" ]] || die "dataset metadata missing/empty: $DATASET/build_meta.json"
[[ -s "$DATASET/data.yaml" ]] || die "dataset YAML missing/empty: $DATASET/data.yaml"
[[ -s "$BASE" ]] || die "base weights missing/empty: $BASE"
[[ -s "src/detection/train.py" ]] || die "training entrypoint missing: src/detection/train.py"
for split in train val; do
  [[ -d "$DATASET/images/$split" ]] || die "missing dataset split: $DATASET/images/$split"
  [[ -d "$DATASET/labels/$split" ]] || die "missing dataset split: $DATASET/labels/$split"
done
grep -Eq '^[[:space:]]*path[[:space:]]*:' "$DATASET/data.yaml" \
  || die "data.yaml has no path: field to rewrite on the remote copy"

DATASET_BASENAME="$(basename "$DATASET")"
[[ "$DATASET_BASENAME" =~ ^[A-Za-z0-9][A-Za-z0-9._-]*$ ]] \
  || die "unsafe dataset basename for Windows path: $DATASET_BASENAME"

REMOTE_DATASET="$REMOTE/datasets/$DATASET_BASENAME"
REMOTE_ARCHIVE="$REMOTE/eth3m_pilot_${NAME}.tar"
REMOTE_BATCH="$REMOTE/launch_eth3m_short_pilot_${NAME}.cmd"
REMOTE_LOG="$REMOTE/logs/$NAME.log"

say "package dataset"
TMP_TAR="$(mktemp -t fable_eth3m_pilot_dataset)"
# One archive avoids per-file SSH round trips.  Excluding caches makes the copy
# deterministic; COPYFILE_DISABLE prevents macOS ._* files becoming apparent
# chart images on Windows.
COPYFILE_DISABLE=1 tar -cf "$TMP_TAR" \
  --exclude='*.npy' --exclude='*.cache' --exclude='._*' \
  -C "$(dirname "$DATASET")" "$DATASET_BASENAME"
printf '  archive: %s\n' "$(du -h "$TMP_TAR" | awk '{print $1}')"

TMP_CMD="$(mktemp -t fable_eth3m_pilot_cmd)"
{
  printf '@echo off\r\n'
  printf 'setlocal\r\n'
  printf '> C:\\fable\\logs\\%s.log echo [launcher] queued %%DATE%% %%TIME%%\r\n' "$NAME"
  if [[ -n "$WAIT_PID" ]]; then
    printf '>> C:\\fable\\logs\\%s.log echo [launcher] waiting for PID %s\r\n' "$NAME" "$WAIT_PID"
    printf 'powershell.exe -NoLogo -NoProfile -NonInteractive -Command "Wait-Process -Id %s -ErrorAction SilentlyContinue" >> C:\\fable\\logs\\%s.log 2>&1\r\n' "$WAIT_PID" "$NAME"
    printf '>> C:\\fable\\logs\\%s.log echo [launcher] PID %s exited; starting training %%DATE%% %%TIME%%\r\n' "$NAME" "$WAIT_PID"
  else
    printf '>> C:\\fable\\logs\\%s.log echo [launcher] starting training %%DATE%% %%TIME%%\r\n' "$NAME"
  fi
  printf 'cd /d C:\\fable\r\n'
  printf 'C:\\fable\\.venv\\Scripts\\python.exe -u C:\\fable\\train_eth3m_short_pilot.py --data C:/fable/datasets/%s/data.yaml --model C:/fable/eth3m_pilot_base.pt --epochs %s --patience %s --imgsz 960 --batch 8 --device 0 --workers 4 --cache false --no-finetune --name %s >> C:\\fable\\logs\\%s.log 2>&1\r\n' \
    "$DATASET_BASENAME" "$EPOCHS" "$PATIENCE" "$NAME" "$NAME"
  printf 'exit /b %%ERRORLEVEL%%\r\n'
} >"$TMP_CMD"

say "sync immutable inputs to $HOST:$REMOTE"
"${SCP[@]}" "$TMP_TAR" "$HOST:$REMOTE_ARCHIVE" \
  || die "failed to copy dataset archive"
"${SCP[@]}" "src/detection/train.py" "$HOST:$REMOTE/train_eth3m_short_pilot.py" \
  || die "failed to copy training entrypoint"
"${SCP[@]}" "$BASE" "$HOST:$REMOTE/eth3m_pilot_base.pt" \
  || die "failed to copy base weights"
"${SCP[@]}" "$TMP_CMD" "$HOST:$REMOTE_BATCH" \
  || die "failed to copy detached batch launcher"

# The archive is first verified in a staging directory, then replaces only the
# exact dataset basename.  data.yaml builders commonly record an absolute Mac
# path; rewrite only the first path: line in the remote copy.  The local source
# and its build_meta.json remain untouched.
prepare_ps="
\$ErrorActionPreference = 'Stop'
\$archive = '$REMOTE_ARCHIVE'
\$datasets = '$REMOTE/datasets'
\$stage = '$REMOTE/datasets/.eth3m_stage_$NAME'
\$incoming = \$stage + '/$DATASET_BASENAME'
\$target = '$REMOTE_DATASET'
New-Item -ItemType Directory -Force -Path \$datasets, '$REMOTE/logs' | Out-Null
try {
  Remove-Item -LiteralPath \$stage -Recurse -Force -ErrorAction SilentlyContinue
  New-Item -ItemType Directory -Force -Path \$stage | Out-Null
  & tar.exe -xf \$archive -C \$stage
  if (\$LASTEXITCODE -ne 0) { throw ('tar failed with exit code ' + \$LASTEXITCODE) }
  foreach (\$required in @('data.yaml', 'build_meta.json')) {
    if (-not (Test-Path -LiteralPath (Join-Path \$incoming \$required))) {
      throw ('archive missing ' + \$required)
    }
  }
  Remove-Item -LiteralPath \$target -Recurse -Force -ErrorAction SilentlyContinue
  Move-Item -LiteralPath \$incoming -Destination \$target

  \$yaml = Join-Path \$target 'data.yaml'
  [string[]]\$lines = [System.IO.File]::ReadAllLines(\$yaml)
  \$rewritten = \$false
  for (\$i = 0; \$i -lt \$lines.Length; \$i++) {
    if (-not \$rewritten -and \$lines[\$i] -match '^\s*path\s*:') {
      \$lines[\$i] = 'path: $REMOTE_DATASET'
      \$rewritten = \$true
    }
  }
  if (-not \$rewritten) { throw 'remote data.yaml has no path: line' }
  [System.IO.File]::WriteAllLines(
    \$yaml, \$lines, [System.Text.UTF8Encoding]::new(\$false))
} finally {
  Remove-Item -LiteralPath \$archive -Force -ErrorAction SilentlyContinue
  Remove-Item -LiteralPath \$stage -Recurse -Force -ErrorAction SilentlyContinue
}
Write-Output ('dataset=' + \$target)
Write-Output ((Get-Content -LiteralPath (Join-Path \$target 'data.yaml') |
  Where-Object { \$_ -match '^\s*path\s*:' } | Select-Object -First 1))
"
remote_ps <<<"$prepare_ps" | tr -d '\r'

# Local copies are no longer needed after successful upload.  Clearing their
# variables makes the EXIT trap a no-op for these already-removed exact paths.
rm -f -- "$TMP_TAR" "$TMP_CMD"
TMP_TAR=""
TMP_CMD=""

say "start detached WMI job"
start_ps="
\$ErrorActionPreference = 'Stop'
\$commandLine = 'cmd.exe /d /c \"C:\\fable\\launch_eth3m_short_pilot_${NAME}.cmd\"'
\$result = Invoke-CimMethod -ClassName Win32_Process -MethodName Create -Arguments @{CommandLine=\$commandLine}
if (\$result.ReturnValue -ne 0) {
  throw ('Win32_Process.Create failed, return value=' + \$result.ReturnValue)
}
Write-Output ('PID=' + \$result.ProcessId)
"
START_OUTPUT="$(remote_ps <<<"$start_ps" | tr -d '\r')"
printf '%s\n' "$START_OUTPUT"
[[ "$START_OUTPUT" =~ PID=([0-9]+) ]] || die "WMI returned no PID"

printf '  log: %s\n' "$REMOTE_LOG"
if [[ -n "$WAIT_PID" ]]; then
  printf '  queued behind PID: %s\n' "$WAIT_PID"
fi
printf '  status: '
printf 'FABLE_3060_HOST=%q bash %q --status --name %q\n' "$HOST" "$0" "$NAME"
printf '\nStarted only; this launcher will not wait, fetch, evaluate, promote, or activate.\n'
