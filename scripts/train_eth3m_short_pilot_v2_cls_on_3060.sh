#!/bin/bash
# Ship and start the preregistered ETH 3m v2 classifier on the Windows RTX 3060.
# The job is detached through WMI, may run beside the owner-authorized existing
# GPU process, and never waits, retries, evaluates holdout, promotes, or writes
# ACTIVE.  A CUDA OOM is recorded as one nonzero exit and is not auto-retried.
set -euo pipefail

cd "$(dirname "$0")/.."

HOST="${FABLE_3060_HOST:-}"
REMOTE="${FABLE_3060_REMOTE:-C:/fable}"
REMOTE_WIN="${REMOTE//\//\\}"
LOCAL_PY=".venv/bin/python"
REMOTE_PY="$REMOTE/.venv/Scripts/python.exe"
DATASET="datasets/eth_3m_short_pilot_v2_cls_letterbox960"
MODEL="models/yolo11n-cls.pt"
PREREG="analysis/eth3m_short_pilot_v2_cls_prereg.json"
NAME="eth3m_short_pilot_v2_cls_diag_20260730"
MODE="run"

SSH=(ssh -o BatchMode=yes -o ConnectTimeout=15)
SCP=(scp -o BatchMode=yes -o ConnectTimeout=15 -q)
TMP_TAR=""
TMP_CMD=""
cleanup() {
  set +e
  [[ -n "$TMP_TAR" && -f "$TMP_TAR" ]] && rm -f -- "$TMP_TAR"
  [[ -n "$TMP_CMD" && -f "$TMP_CMD" ]] && rm -f -- "$TMP_CMD"
}
trap cleanup EXIT

say() { printf '\n\033[1;36m=== %s ===\033[0m\n' "$*"; }
die() { printf '\033[1;31m[X] %s\033[0m\n' "$*" >&2; exit 1; }
usage() {
  cat <<'EOF'
Usage: bash scripts/train_eth3m_short_pilot_v2_cls_on_3060.sh [--check|--status]

  Required environment: FABLE_3060_HOST=user@current-ip

  --check    SSH/version/CUDA check only; no writes and no training
  --status   read-only matching process, log, exit code, and best.pt status
  run        no flags; validate, sync immutable inputs, and start detached WMI job
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --check) [[ "$MODE" == run ]] || die "choose one mode"; MODE=check; shift ;;
    --status) [[ "$MODE" == run ]] || die "choose one mode"; MODE=status; shift ;;
    -h|--help) usage; exit 0 ;;
    *) die "unknown argument: $1" ;;
  esac
done

[[ -n "$HOST" ]] || die \
  "FABLE_3060_HOST is required; the 3060 DHCP address must never be guessed"

remote_ps() {
  # PowerShell 5 behind Windows OpenSSH can return success after executing only
  # the first line of `-Command -`.  Encode the complete stdin program as one
  # UTF-16LE command so multiline staging/status scripts are atomic to the
  # remote parser.  Python's encoder emits a single unwrapped base64 token.
  local encoded
  [[ -x "$LOCAL_PY" ]] || die "missing local Python for PowerShell encoding: $LOCAL_PY"
  encoded="$("$LOCAL_PY" -c \
    'import base64,sys; data=sys.stdin.buffer.read().decode("utf-8"); sys.stdout.write(base64.b64encode(data.encode("utf-16le")).decode("ascii"))')"
  [[ -n "$encoded" ]] || die "refusing to execute an empty remote PowerShell program"
  "${SSH[@]}" "$HOST" \
    "powershell.exe -NoLogo -NoProfile -NonInteractive -EncodedCommand $encoded"
}

check_remote() {
  local local_v remote_v cuda_ps cuda_info
  say "3060 connectivity, dependency parity, and CUDA"
  [[ -x "$LOCAL_PY" ]] || die "missing local Python: $LOCAL_PY"
  remote_ps >/dev/null <<PS || die "SSH/PowerShell unavailable: $HOST"
\$ErrorActionPreference = 'Stop'
if (-not (Test-Path -LiteralPath '$REMOTE')) { throw 'missing remote root' }
Write-Output 'ssh-ok'
PS
  local_v="$($LOCAL_PY -c 'import torch,torchvision,ultralytics,numpy;print(torch.__version__.split("+")[0],torchvision.__version__.split("+")[0],ultralytics.__version__,numpy.__version__,sep="|")')"
  remote_v="$(remote_ps <<PS | tr -d '\r\n'
& '$REMOTE_PY' -c 'import torch,torchvision,ultralytics,numpy;print(torch.__version__.split(chr(43))[0],torchvision.__version__.split(chr(43))[0],ultralytics.__version__,numpy.__version__,sep=chr(124))'
if (\$LASTEXITCODE -ne 0) { exit \$LASTEXITCODE }
PS
)"
  printf '  Mac : %s\n  3060: %s\n' "$local_v" "$remote_v"
  [[ "$local_v" == "$remote_v" ]] || die "torch/torchvision/ultralytics/numpy mismatch"
  cuda_ps="& '$REMOTE_PY' -c 'import torch;print(torch.cuda.is_available(),torch.cuda.get_device_name(0),round(torch.cuda.get_device_properties(0).total_memory/1024**3,1),sep=chr(124))'"
  cuda_info="$(remote_ps <<<"$cuda_ps" | tr -d '\r\n')"
  [[ "$cuda_info" == True\|* ]] || die "remote CUDA unavailable: $cuda_info"
  printf '  CUDA: %s\n' "$cuda_info"
}

show_status() {
  remote_ps <<PS
\$ErrorActionPreference = 'Stop'
Write-Output '=== matching classifier processes ==='
\$p = @(Get-CimInstance Win32_Process | Where-Object {
  \$_.CommandLine -and \$_.CommandLine -like '*$NAME*' -and
  (\$_.CommandLine -like '*train_eth3m_short_pilot_v2_cls*' -or \$_.CommandLine -like '*launch_eth3m_v2_cls*')
})
if (\$p.Count) {
  \$p | Select-Object ProcessId,ParentProcessId,CreationDate,Name,CommandLine |
    Format-List | Out-String -Width 4096 | Write-Output
} else { Write-Output '(none)' }
Write-Output '=== log tail ==='
\$log = '$REMOTE/logs/$NAME.log'
if (Test-Path -LiteralPath \$log) { Get-Content -LiteralPath \$log -Tail 60 } else { Write-Output '(missing)' }
Write-Output '=== exit code ==='
\$exit = '$REMOTE/logs/$NAME.exit_code'
if (Test-Path -LiteralPath \$exit) { Get-Content -LiteralPath \$exit } else { Write-Output '(running or not started)' }
Write-Output '=== best.pt ==='
\$best = '$REMOTE/runs/classify/$NAME/weights/best.pt'
if (Test-Path -LiteralPath \$best) {
  Get-Item -LiteralPath \$best | Select-Object FullName,Length,LastWriteTime |
    Format-List | Out-String -Width 4096 | Write-Output
} else { Write-Output '(missing)' }
PS
}

if [[ "$MODE" == status ]]; then show_status; exit 0; fi
check_remote
if [[ "$MODE" == check ]]; then printf '\nCheck passed; nothing was written or started.\n'; exit 0; fi

say "local immutable-input gates"
[[ -s "$MODEL" ]] || die "missing pretrained checkpoint: $MODEL"
[[ -s "$PREREG" ]] || die "missing preregistration: $PREREG"
[[ -d "$DATASET" ]] || die "missing prepared dataset: $DATASET"
PYTHONPATH=. "$LOCAL_PY" scripts/prepare_eth3m_short_pilot_v2_cls.py --verify-only --output "$DATASET"
PYTHONPATH=. "$LOCAL_PY" scripts/train_eth3m_short_pilot_v2_cls.py \
  --data "$DATASET" --model "$MODEL" --prereg "$PREREG" --name "$NAME" --preflight-only

DATASET_BASE="$(basename "$DATASET")"
MODEL_SHA="$(shasum -a 256 "$MODEL" | awk '{print $1}')"
CODE_SHA="$(shasum -a 256 src/detection/eth3m_v2_classification.py | awk '{print $1}')"
TRAINER_SHA="$(shasum -a 256 scripts/train_eth3m_short_pilot_v2_cls.py | awk '{print $1}')"
PREREG_SHA="$(shasum -a 256 "$PREREG" | awk '{print $1}')"
DATASET_META_SHA="$(shasum -a 256 "$DATASET/build_meta.json" | awk '{print $1}')"
DATASET_MANIFEST_SHA="$(shasum -a 256 "$DATASET/manifest.csv" | awk '{print $1}')"
REMOTE_DATASET="$REMOTE/datasets/$DATASET_BASE"
REMOTE_MODEL="$REMOTE/inputs/$MODEL_SHA/yolo11n-cls.pt"
REMOTE_TRAINER="$REMOTE/scripts/train_eth3m_short_pilot_v2_cls_$NAME.py"
REMOTE_MODULE="$REMOTE/src/detection/eth3m_v2_classification.py"
REMOTE_PREREG="$REMOTE/analysis/eth3m_short_pilot_v2_cls_prereg.json"
REMOTE_BATCH="$REMOTE/launch_eth3m_v2_cls_$NAME.cmd"
REMOTE_LOG="$REMOTE/logs/$NAME.log"

say "package the 137-image train/val-only square dataset"
TMP_TAR="$(mktemp -t fable_eth3m_v2_cls)"
COPYFILE_DISABLE=1 tar -cf "$TMP_TAR" --exclude='*.cache' --exclude='*.npy' --exclude='._*' \
  -C "$(dirname "$DATASET")" "$DATASET_BASE"
TMP_CMD="$(mktemp -t fable_eth3m_v2_cls_cmd)"
{
  printf '@echo off\r\nsetlocal\r\n'
  printf '> C:\\fable\\logs\\%s.log echo [launcher] started %%DATE%% %%TIME%%\r\n' "$NAME"
  # An absolute script path makes Python put C:\fable\scripts (not C:\fable)
  # on sys.path.  Set the project root explicitly before importing src.*.
  printf 'set "PYTHONPATH=%s"\r\n' "$REMOTE_WIN"
  printf 'cd /d C:\\fable\r\n'
  printf 'C:\\fable\\.venv\\Scripts\\python.exe -u C:\\fable\\scripts\\train_eth3m_short_pilot_v2_cls_%s.py --data C:/fable/datasets/%s --model C:/fable/inputs/%s/yolo11n-cls.pt --prereg C:/fable/analysis/eth3m_short_pilot_v2_cls_prereg.json --name %s >> C:\\fable\\logs\\%s.log 2>&1\r\n' \
    "$NAME" "$DATASET_BASE" "$MODEL_SHA" "$NAME" "$NAME"
  printf 'set RC=%%ERRORLEVEL%%\r\n'
  printf '>> C:\\fable\\logs\\%s.log echo [launcher] exit_code=%%RC%% %%DATE%% %%TIME%%\r\n' "$NAME"
  printf '> C:\\fable\\logs\\%s.exit_code echo %%RC%%\r\n' "$NAME"
  printf 'exit /b %%RC%%\r\n'
} >"$TMP_CMD"
BATCH_SHA="$(shasum -a 256 "$TMP_CMD" | awk '{print $1}')"
STAGE_SENTINEL="STAGE_OK|$NAME|$MODEL_SHA|$BATCH_SHA"

say "sync to unique remote staging paths"
"${SCP[@]}" "$TMP_TAR" "$HOST:$REMOTE/incoming_$NAME.tar"
"${SCP[@]}" "$MODEL" "$HOST:$REMOTE/incoming_${NAME}_model.pt"
"${SCP[@]}" scripts/train_eth3m_short_pilot_v2_cls.py "$HOST:$REMOTE/incoming_${NAME}_trainer.py"
"${SCP[@]}" src/detection/eth3m_v2_classification.py "$HOST:$REMOTE/incoming_${NAME}_module.py"
"${SCP[@]}" "$PREREG" "$HOST:$REMOTE/incoming_${NAME}_prereg.json"
"${SCP[@]}" "$TMP_CMD" "$HOST:$REMOTE/incoming_${NAME}.cmd"

STAGE_OUTPUT="$(remote_ps <<PS | tr -d '\r'
\$ErrorActionPreference = 'Stop'
\$run = '$REMOTE/runs/classify/$NAME'
\$log = '$REMOTE_LOG'
\$exit = '$REMOTE/logs/$NAME.exit_code'
if ((Test-Path -LiteralPath \$run) -or (Test-Path -LiteralPath \$log) -or (Test-Path -LiteralPath \$exit)) {
  throw 'refusing to overwrite an existing run/log/exit receipt'
}
New-Item -ItemType Directory -Force -Path '$REMOTE/datasets','$REMOTE/inputs/$MODEL_SHA','$REMOTE/scripts','$REMOTE/src/detection','$REMOTE/analysis','$REMOTE/logs' | Out-Null

function Assert-Hash([string]\$Path, [string]\$Expected, [string]\$Label) {
  if (-not (Test-Path -LiteralPath \$Path -PathType Leaf)) { throw (\$Label + ' missing: ' + \$Path) }
  \$actual = (Get-FileHash -Algorithm SHA256 -LiteralPath \$Path).Hash.ToLowerInvariant()
  if (\$actual -ne \$Expected) { throw (\$Label + ' hash mismatch: ' + \$actual) }
}
function Finalize-Immutable([string]\$Incoming, [string]\$Target, [string]\$Expected, [string]\$Label) {
  Assert-Hash \$Incoming \$Expected ('incoming ' + \$Label)
  if (Test-Path -LiteralPath \$Target) {
    Assert-Hash \$Target \$Expected ('existing ' + \$Label)
    Remove-Item -LiteralPath \$Incoming -Force
  } else {
    Move-Item -LiteralPath \$Incoming -Destination \$Target
    Assert-Hash \$Target \$Expected ('final ' + \$Label)
  }
}
function Assert-Dataset([string]\$Root) {
  if (-not (Test-Path -LiteralPath \$Root -PathType Container)) { throw ('dataset missing: ' + \$Root) }
  Assert-Hash (Join-Path \$Root 'build_meta.json') '$DATASET_META_SHA' 'dataset build_meta'
  Assert-Hash (Join-Path \$Root 'manifest.csv') '$DATASET_MANIFEST_SHA' 'dataset manifest'
}

if (Test-Path -LiteralPath '$REMOTE_DATASET') {
  Assert-Dataset '$REMOTE_DATASET'
  Remove-Item -LiteralPath '$REMOTE/incoming_$NAME.tar' -Force
} else {
  \$stage = '$REMOTE/datasets/.stage_$NAME'
  try {
    Remove-Item -LiteralPath \$stage -Recurse -Force -ErrorAction SilentlyContinue
    New-Item -ItemType Directory -Path \$stage | Out-Null
    & tar.exe -xf '$REMOTE/incoming_$NAME.tar' -C \$stage
    if (\$LASTEXITCODE -ne 0) { throw 'dataset tar extraction failed' }
    \$incomingDataset = Join-Path \$stage '$DATASET_BASE'
    Assert-Dataset \$incomingDataset
    Move-Item -LiteralPath \$incomingDataset -Destination '$REMOTE_DATASET'
    Assert-Dataset '$REMOTE_DATASET'
  } finally {
    Remove-Item -LiteralPath \$stage -Recurse -Force -ErrorAction SilentlyContinue
    Remove-Item -LiteralPath '$REMOTE/incoming_$NAME.tar' -Force -ErrorAction SilentlyContinue
  }
}
Finalize-Immutable '$REMOTE/incoming_${NAME}_model.pt' '$REMOTE_MODEL' '$MODEL_SHA' 'pretrained model'
Finalize-Immutable '$REMOTE/incoming_${NAME}_trainer.py' '$REMOTE_TRAINER' '$TRAINER_SHA' 'trainer'
Finalize-Immutable '$REMOTE/incoming_${NAME}_module.py' '$REMOTE_MODULE' '$CODE_SHA' 'classifier module'
Finalize-Immutable '$REMOTE/incoming_${NAME}_prereg.json' '$REMOTE_PREREG' '$PREREG_SHA' 'preregistration'
Finalize-Immutable '$REMOTE/incoming_${NAME}.cmd' '$REMOTE_BATCH' '$BATCH_SHA' 'batch launcher'
Write-Output '$STAGE_SENTINEL'
PS
)"
printf '%s\n' "$STAGE_OUTPUT"
STAGE_CONFIRMED=0
while IFS= read -r line; do
  [[ "$line" == "$STAGE_SENTINEL" ]] && STAGE_CONFIRMED=1
done <<<"$STAGE_OUTPUT"
[[ "$STAGE_CONFIRMED" == 1 ]] || die "remote staging returned no exact success sentinel; WMI will not start"

rm -f -- "$TMP_TAR" "$TMP_CMD"
TMP_TAR=""
TMP_CMD=""

say "start one detached WMI job (no wait, kill, or retry)"
START="$(remote_ps <<PS | tr -d '\r'
\$ErrorActionPreference = 'Stop'
\$cmd = 'cmd.exe /d /c "C:\\fable\\launch_eth3m_v2_cls_${NAME}.cmd"'
\$r = Invoke-CimMethod -ClassName Win32_Process -MethodName Create -Arguments @{CommandLine=\$cmd}
if (\$r.ReturnValue -ne 0) { throw ('WMI create failed: ' + \$r.ReturnValue) }
Write-Output ('PID=' + \$r.ProcessId)
PS
)"
printf '%s\n  log: %s\n' "$START" "$REMOTE_LOG"
[[ "$START" =~ PID=([0-9]+) ]] || die "WMI returned no PID"
printf '  status: FABLE_3060_HOST=%q bash %q --status\n' "$HOST" "$0"
printf '\nStarted only. No wait/retry/evaluation/promotion/ACTIVE action was performed.\n'
