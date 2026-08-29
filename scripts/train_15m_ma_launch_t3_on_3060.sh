#!/bin/bash
# Ship, start, inspect and fetch the Owner-authorized 15m t-3 YOLO run.
#
# The RTX 3060 is a disposable compute worker: the Mac owns the immutable
# dataset, receipts and final report.  This launcher never promotes, deploys,
# touches ACTIVE/frozen, evaluates holdout or changes trading state.
set -euo pipefail

cd "$(dirname "$0")/.."

HOST="${FABLE_3060_HOST:-}"
REMOTE="${FABLE_3060_REMOTE:-C:/fable}"
LOCAL_PY=".venv/bin/python"
REMOTE_PY="$REMOTE/.venv/Scripts/python.exe"
DATASET="${FABLE_T3_DATASET:-datasets/ma_launch_t3_10000_v1}"
MODEL="models/yolo11s.pt"
TRAINER="src/detection/train.py"
PREFLIGHT="scripts/windows/verify_yolo_dataset.py"
EXPERIMENT_ID="${FABLE_T3_EXPERIMENT_ID:-exp-15m-ma-launch-t3-yolo10000-v1}"
PREREG="${FABLE_T3_PREREG:-experiments/active/exp-15m-ma-launch-t3-yolo10000-v1/preregistration.json}"
BUILD_RECEIPT="${FABLE_T3_BUILD_RECEIPT:-experiments/active/exp-15m-ma-launch-t3-yolo10000-v1/results/dataset_build_receipt.json}"
QA_RECEIPT="${FABLE_T3_QA_RECEIPT:-experiments/active/exp-15m-ma-launch-t3-yolo10000-v1/results/dataset_qa_receipt.json}"
NAME="${FABLE_T3_RUN_NAME:-ma_launch_t3_10000_v1_y11s_ft}"
IMGSZ="${FABLE_T3_IMGSZ:-960}"
EPOCHS="${FABLE_T3_EPOCHS:-40}"
PATIENCE="${FABLE_T3_PATIENCE:-10}"
BATCH="${FABLE_T3_BATCH:-8}"
WAIT_FOR_RUN="${FABLE_T3_WAIT_FOR_RUN:-}"
REMOTE_DATASET_NAME="${FABLE_T3_REMOTE_DATASET_NAME:-ma_launch_t3_10000_v1}"
LOCAL_OUTPUT_ROOT="${FABLE_T3_LOCAL_OUTPUT_ROOT:-analysis/output/ma_launch_t3_10000_v1}"
DATASET_IMAGE_COUNT="${FABLE_T3_DATASET_IMAGE_COUNT:-36812}"
STRICT_PREFLIGHT="${FABLE_T3_STRICT_PREFLIGHT:-false}"
EXPERIMENT_RESULTS="$(dirname "$PREREG")/results"
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
  sed -n '2,9p' "$0"
  printf '%s\n' \
    'Required: FABLE_3060_HOST=Administrator@<current-ip>' \
    'Modes: --check | --status | --fetch | run (default)'
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --check) MODE=check; shift ;;
    --status) MODE=status; shift ;;
    --fetch) MODE=fetch; shift ;;
    -h|--help) usage; exit 0 ;;
    *) die "unknown argument: $1" ;;
  esac
done

[[ -n "$HOST" ]] || die "FABLE_3060_HOST is required; DHCP addresses are never guessed"
[[ -x "$LOCAL_PY" ]] || die "missing local Python: $LOCAL_PY"
[[ "$STRICT_PREFLIGHT" == true || "$STRICT_PREFLIGHT" == false ]] \
  || die "FABLE_T3_STRICT_PREFLIGHT must be true or false"
[[ "$EPOCHS" =~ ^[1-9][0-9]*$ ]] || die "FABLE_T3_EPOCHS must be a positive integer"
[[ "$PATIENCE" =~ ^[0-9]+$ ]] || die "FABLE_T3_PATIENCE must be a non-negative integer"
[[ "$BATCH" =~ ^[1-9][0-9]*$ ]] || die "FABLE_T3_BATCH must be a positive integer"

remote_ps() {
  local encoded
  encoded="$("$LOCAL_PY" -c \
    'import base64,sys; s=sys.stdin.buffer.read().decode("utf-8"); print(base64.b64encode(s.encode("utf-16le")).decode("ascii"))')"
  [[ -n "$encoded" ]] || die "refusing to execute empty remote PowerShell"
  "${SSH[@]}" "$HOST" \
    "powershell.exe -NoLogo -NoProfile -NonInteractive -EncodedCommand $encoded"
}

check_remote() {
  local local_v remote_v cuda_info gpu_jobs free_bytes
  say "3060 connectivity, dependency parity, CUDA and current GPU jobs"
  remote_ps >/dev/null <<PS || die "SSH/PowerShell unavailable: $HOST"
\$ErrorActionPreference = 'Stop'
if (-not (Test-Path -LiteralPath '$REMOTE')) { throw 'missing C:/fable' }
Write-Output 'ssh-ok'
PS
  local_v="$("$LOCAL_PY" -c 'import torch,torchvision,ultralytics,numpy;print(torch.__version__.split("+")[0],torchvision.__version__.split("+")[0],ultralytics.__version__,numpy.__version__,sep="|")')"
  remote_v="$(remote_ps <<PS | tr -d '\r\n'
& '$REMOTE_PY' -c 'import torch,torchvision,ultralytics,numpy;print(torch.__version__.split(chr(43))[0],torchvision.__version__.split(chr(43))[0],ultralytics.__version__,numpy.__version__,sep=chr(124))'
if (\$LASTEXITCODE -ne 0) { exit \$LASTEXITCODE }
PS
)"
  printf '  Mac : %s\n  3060: %s\n' "$local_v" "$remote_v"
  [[ "$local_v" == "$remote_v" ]] || die "version mismatch; results would not be comparable"
  cuda_info="$(remote_ps <<PS | tr -d '\r\n'
& '$REMOTE_PY' -c 'import torch,torchvision;b=torch.tensor([[0.,0.,10.,10.],[1.,1.,9.,9.]]).cuda();s=torch.tensor([.9,.8]).cuda();print(torch.cuda.is_available(),torch.cuda.get_device_name(0),round(torch.cuda.get_device_properties(0).total_memory/1024**3,1),torchvision.ops.nms(b,s,.5).cpu().tolist()==[0],sep=chr(124))'
if (\$LASTEXITCODE -ne 0) { exit \$LASTEXITCODE }
PS
)"
  [[ "$cuda_info" == True\|*\|True ]] || die "CUDA/NMS unavailable: $cuda_info"
  printf '  CUDA: %s\n' "$cuda_info"
  free_bytes="$(remote_ps <<PS | tr -d '\r\n '
\$drive = Get-PSDrive -Name C
Write-Output ([int64]\$drive.Free)
PS
)"
  [[ "$free_bytes" =~ ^[0-9]+$ ]] || die "could not read remote C: free bytes: $free_bytes"
  (( free_bytes >= 20 * 1024 * 1024 * 1024 )) || die "remote C: has less than 20 GiB free"
  "$LOCAL_PY" -c 'import sys; print(f"  C: free: {int(sys.argv[1]) / 1024**3:.1f} GiB")' "$free_bytes"
  gpu_jobs="$(remote_ps <<PS | tr -d '\r'
\$jobs = & nvidia-smi --query-compute-apps=pid,process_name,used_memory --format=csv,noheader 2>\$null
if (\$LASTEXITCODE -ne 0) { exit \$LASTEXITCODE }
if (\$jobs) { \$jobs } else { '(none)' }
PS
)"
  printf '  GPU jobs:\n%s\n' "$gpu_jobs"
}

show_status() {
  remote_ps <<PS
\$ErrorActionPreference = 'Stop'
Write-Output '=== matching processes ==='
\$p = @(Get-CimInstance Win32_Process | Where-Object {
  \$_.CommandLine -and \$_.CommandLine -like '*$NAME*'
})
if (\$p.Count) {
  \$p | Select-Object ProcessId,ParentProcessId,CreationDate,Name,CommandLine |
    Format-List | Out-String -Width 4096 | Write-Output
} else { Write-Output '(none)' }
Write-Output '=== log tail ==='
\$log = '$REMOTE/logs/$NAME.log'
if (Test-Path -LiteralPath \$log) { Get-Content -LiteralPath \$log -Tail 70 } else { '(missing)' }
Write-Output '=== exit code ==='
\$exit = '$REMOTE/logs/$NAME.exit_code'
if (Test-Path -LiteralPath \$exit) { Get-Content -LiteralPath \$exit } else { '(running or not started)' }
Write-Output '=== best.pt ==='
\$best = '$REMOTE/runs/detect/$NAME/weights/best.pt'
if (Test-Path -LiteralPath \$best) {
  Get-Item -LiteralPath \$best | Select-Object FullName,Length,LastWriteTime |
    Format-List | Out-String -Width 4096 | Write-Output
} else { '(missing)' }
PS
}

fetch_run() {
  local local_run remote_run remote_scp exit_code
  local_run="$LOCAL_OUTPUT_ROOT/$NAME"
  remote_run="$REMOTE/runs/detect/$NAME"
  remote_scp="/C:/fable/runs/detect/$NAME"
  exit_code="$(remote_ps <<PS | tr -d '\r\n '
\$p = '$REMOTE/logs/$NAME.exit_code'
if (-not (Test-Path -LiteralPath \$p)) { throw 'run has no exit code yet' }
Get-Content -LiteralPath \$p
PS
)"
  [[ "$exit_code" == 0 ]] || die "remote run exit code is $exit_code"
  mkdir -p "$local_run/weights"
  "${SCP[@]}" "$HOST:$remote_scp/weights/best.pt" "$local_run/weights/best.pt"
  for file in args.yaml results.csv; do
    "${SCP[@]}" "$HOST:$remote_scp/$file" "$local_run/$file"
  done
  "${SCP[@]}" "$HOST:/C:/fable/logs/$NAME.log" "$local_run/train.log"
  if [[ "$STRICT_PREFLIGHT" == true ]]; then
    mkdir -p "$EXPERIMENT_RESULTS"
    "${SCP[@]}" "$HOST:/C:/fable/experiments/$NAME/remote_dataset_preflight.json" \
      "$EXPERIMENT_RESULTS/remote_dataset_preflight.json"
  fi
  remote_ps >"$local_run/remote_training_receipt.txt" <<PS
\$files = @(
  '$remote_run/weights/best.pt',
  '$remote_run/args.yaml',
  '$remote_run/results.csv',
  '$REMOTE/logs/$NAME.log',
  '$REMOTE/logs/$NAME.exit_code',
  '$REMOTE/experiments/$NAME/remote_dataset_preflight.json'
)
foreach (\$p in \$files) {
  if (Test-Path -LiteralPath \$p -PathType Leaf) {
    \$f = Get-Item -LiteralPath \$p
    \$h = (Get-FileHash -Algorithm SHA256 -LiteralPath \$p).Hash.ToLowerInvariant()
    Write-Output (\$p + '|' + \$f.Length + '|' + \$h + '|' + \$f.LastWriteTimeUtc.ToString('o'))
  } else { Write-Output (\$p + '|missing') }
}
PS
  printf 'Fetched to %s\n' "$local_run"
}

if [[ "$MODE" == status ]]; then show_status; exit 0; fi
if [[ "$MODE" == fetch ]]; then fetch_run; exit 0; fi
check_remote
if [[ "$MODE" == check ]]; then printf '\nCheck passed; nothing was staged or started.\n'; exit 0; fi

say "local immutable-input gates"
for path in "$MODEL" "$TRAINER" "$PREFLIGHT" "$PREREG" "$BUILD_RECEIPT" "$QA_RECEIPT" \
  "$DATASET/manifest.jsonl" "$DATASET/build_summary.json" "$DATASET/data.yaml"; do
  [[ -s "$path" ]] || die "missing required input: $path"
done
QA_PASSED="$("$LOCAL_PY" -c 'import json,sys;print(json.load(open(sys.argv[1], encoding="utf-8"))["passed"])' "$QA_RECEIPT")"
[[ "$QA_PASSED" == True ]] || die "dataset QA receipt is not passed"
PREREG_GATE="$("$LOCAL_PY" -c '
import json,sys
p=json.load(open(sys.argv[1], encoding="utf-8"))
assert p["experiment_id"] == sys.argv[2], "experiment_id mismatch"
assert p["training"]["run_name"] == sys.argv[3], "run_name mismatch"
assert int(p["training"]["imgsz"]) == int(sys.argv[4]), "imgsz mismatch"
assert int(p["training"]["epochs"]) == int(sys.argv[5]), "epochs mismatch"
assert int(p["training"]["patience"]) == int(sys.argv[6]), "patience mismatch"
assert int(p["training"]["batch"]) == int(sys.argv[7]), "batch mismatch"
assert p["training"].get("wait_for_run", "") == sys.argv[8], "wait prerequisite mismatch"
assert p["owner_authorization"]["training_authorized"] is True, "training not authorized"
assert p["safety"]["holdout_read"] is False, "holdout must stay sealed"
assert p["safety"]["promote"] is False, "promotion must stay disabled"
print("PREREG_OK")
' "$PREREG" "$EXPERIMENT_ID" "$NAME" "$IMGSZ" "$EPOCHS" "$PATIENCE" "$BATCH" "$WAIT_FOR_RUN")" || die "preregistration gate failed"
[[ "$PREREG_GATE" == PREREG_OK ]] || die "preregistration gate returned no exact sentinel"

DATASET_BASE="$(basename "$DATASET")"
LAUNCHER_SHA="$(shasum -a 256 "$0" | awk '{print $1}')"
MODEL_SHA="$(shasum -a 256 "$MODEL" | awk '{print $1}')"
TRAINER_SHA="$(shasum -a 256 "$TRAINER" | awk '{print $1}')"
PREFLIGHT_SHA="$(shasum -a 256 "$PREFLIGHT" | awk '{print $1}')"
PREREG_SHA="$(shasum -a 256 "$PREREG" | awk '{print $1}')"
BUILD_SHA="$(shasum -a 256 "$BUILD_RECEIPT" | awk '{print $1}')"
QA_SHA="$(shasum -a 256 "$QA_RECEIPT" | awk '{print $1}')"
MANIFEST_SHA="$(shasum -a 256 "$DATASET/manifest.jsonl" | awk '{print $1}')"
SUMMARY_SHA="$(shasum -a 256 "$DATASET/build_summary.json" | awk '{print $1}')"
YAML_SHA="$(shasum -a 256 "$DATASET/data.yaml" | awk '{print $1}')"
INPUT_GATE="$("$LOCAL_PY" -c '
import json,sys
p=json.load(open(sys.argv[1], encoding="utf-8"))
f=p.get("immutable_inputs")
strict = sys.argv[11] == "true"
if f is not None:
    assert f["manifest_sha256"] == sys.argv[2], "manifest hash mismatch"
    assert f["build_summary_sha256"] == sys.argv[3], "build summary hash mismatch"
    assert f["dataset_build_receipt_sha256"] == sys.argv[4], "build receipt hash mismatch"
    assert f["dataset_qa_receipt_sha256"] == sys.argv[5], "QA receipt hash mismatch"
    if strict or "data_yaml_sha256" in f:
        assert f["data_yaml_sha256"] == sys.argv[8], "data yaml hash mismatch"
assert p["training"]["base_model_sha256"] == sys.argv[6], "model hash mismatch"
if "trainer_sha256" in p["training"]:
    assert p["training"]["trainer_sha256"] == sys.argv[7], "trainer hash mismatch"
if strict:
    assert p["preflight"]["script_sha256"] == sys.argv[9], "preflight hash mismatch"
    assert p["training"]["launcher_sha256"] == sys.argv[10], "launcher hash mismatch"
print("INPUTS_OK")
' "$PREREG" "$MANIFEST_SHA" "$SUMMARY_SHA" "$BUILD_SHA" "$QA_SHA" "$MODEL_SHA" "$TRAINER_SHA" "$YAML_SHA" "$PREFLIGHT_SHA" "$LAUNCHER_SHA" "$STRICT_PREFLIGHT")" || die "immutable input gate failed"
[[ "$INPUT_GATE" == INPUTS_OK ]] || die "immutable input gate returned no exact sentinel"
if [[ "$STRICT_PREFLIGHT" == true ]]; then
  mkdir -p "$EXPERIMENT_RESULTS"
  say "full local dataset preflight"
  LOCAL_PREFLIGHT_OUTPUT="$("$LOCAL_PY" "$PREFLIGHT" \
    --dataset "$DATASET" \
    --prereg "$PREREG" \
    --verify-file-hashes \
    --output "$EXPERIMENT_RESULTS/local_dataset_preflight.json")" \
    || die "local dataset preflight failed"
  printf '%s\n' "$LOCAL_PREFLIGHT_OUTPUT"
  grep -Fq "PREFLIGHT_OK|$MANIFEST_SHA|$DATASET_IMAGE_COUNT|" <<<"$LOCAL_PREFLIGHT_OUTPUT" \
    || die "local preflight returned no expected manifest/count sentinel"
fi
REMOTE_DATASET="$REMOTE/datasets/$REMOTE_DATASET_NAME"
REMOTE_MODEL="$REMOTE/inputs/$MODEL_SHA/yolo11s.pt"
REMOTE_TRAINER="$REMOTE/train_t3_$TRAINER_SHA.py"
REMOTE_PREFLIGHT="$REMOTE/preflight_yolo_$PREFLIGHT_SHA.py"
REMOTE_PREREG="$REMOTE/experiments/$NAME/preregistration.json"
REMOTE_BUILD="$REMOTE/experiments/$NAME/dataset_build_receipt.json"
REMOTE_QA="$REMOTE/experiments/$NAME/dataset_qa_receipt.json"
REMOTE_BATCH="$REMOTE/launch_$NAME.cmd"

say "package ${DATASET_IMAGE_COUNT}-image immutable dataset"
TMP_TAR="$(mktemp -t fable_t3_dataset)"
COPYFILE_DISABLE=1 tar -cf "$TMP_TAR" --exclude='*.cache' --exclude='*.npy' --exclude='._*' \
  -C "$(dirname "$DATASET")" "$DATASET_BASE"
printf '  tar: %s\n' "$(du -h "$TMP_TAR" | cut -f1)"
TMP_CMD="$(mktemp -t fable_t3_cmd)"
{
  printf '@echo off\r\nsetlocal\r\n'
  printf '> C:\\fable\\logs\\%s.log echo [launcher] started %%DATE%% %%TIME%%\r\n' "$NAME"
  printf 'cd /d C:\\fable\r\n'
  if [[ -n "$WAIT_FOR_RUN" ]]; then
    printf '>> C:\\fable\\logs\\%s.log echo [launcher] waiting_for=%s %%DATE%% %%TIME%%\r\n' "$NAME" "$WAIT_FOR_RUN"
    printf 'powershell.exe -NoLogo -NoProfile -NonInteractive -Command "$p=\x27C:\\fable\\logs\\%s.exit_code\x27; while (-not (Test-Path -LiteralPath $p)) { Start-Sleep -Seconds 60 }; $rc=[int]((Get-Content -LiteralPath $p -Raw).Trim()); if ($rc -ne 0) { exit 98 }"\r\n' "$WAIT_FOR_RUN"
    printf 'if errorlevel 1 (\r\n  set RC=98\r\n  goto finalize\r\n)\r\n'
    printf '>> C:\\fable\\logs\\%s.log echo [launcher] prerequisite_ok=%s %%DATE%% %%TIME%%\r\n' "$NAME" "$WAIT_FOR_RUN"
  fi
  printf 'C:\\fable\\.venv\\Scripts\\python.exe -u C:\\fable\\train_t3_%s.py --name %s --model C:/fable/inputs/%s/yolo11s.pt --data C:/fable/datasets/%s/data.yaml --epochs %s --patience %s --batch %s --imgsz %s --seed 0 --finetune --cache false --workers 2 >> C:\\fable\\logs\\%s.log 2>&1\r\n' \
    "$TRAINER_SHA" "$NAME" "$MODEL_SHA" "$REMOTE_DATASET_NAME" "$EPOCHS" "$PATIENCE" "$BATCH" "$IMGSZ" "$NAME"
  printf 'set RC=%%ERRORLEVEL%%\r\n'
  printf ':finalize\r\n'
  printf '>> C:\\fable\\logs\\%s.log echo [launcher] exit_code=%%RC%% %%DATE%% %%TIME%%\r\n' "$NAME"
  printf '> C:\\fable\\logs\\%s.exit_code echo %%RC%%\r\n' "$NAME"
  printf 'exit /b %%RC%%\r\n'
} >"$TMP_CMD"
BATCH_SHA="$(shasum -a 256 "$TMP_CMD" | awk '{print $1}')"

say "sync immutable inputs"
remote_ps >/dev/null <<PS
\$ErrorActionPreference = 'Stop'
if ((Test-Path -LiteralPath '$REMOTE/runs/detect/$NAME') -or
    (Test-Path -LiteralPath '$REMOTE/logs/$NAME.log') -or
    (Test-Path -LiteralPath '$REMOTE/logs/$NAME.exit_code')) {
  throw 'refusing to overwrite existing run/log/exit receipt'
}
New-Item -ItemType Directory -Force -Path '$REMOTE/logs','$REMOTE/datasets','$REMOTE/inputs/$MODEL_SHA','$REMOTE/experiments/$NAME' | Out-Null
PS
"${SCP[@]}" "$TMP_TAR" "$HOST:$REMOTE/incoming_$NAME.tar"
"${SCP[@]}" "$MODEL" "$HOST:$REMOTE/incoming_${NAME}_model.pt"
"${SCP[@]}" "$TRAINER" "$HOST:$REMOTE/incoming_${NAME}_trainer.py"
"${SCP[@]}" "$PREFLIGHT" "$HOST:$REMOTE/incoming_${NAME}_preflight.py"
"${SCP[@]}" "$PREREG" "$HOST:$REMOTE/incoming_${NAME}_prereg.json"
"${SCP[@]}" "$BUILD_RECEIPT" "$HOST:$REMOTE/incoming_${NAME}_build.json"
"${SCP[@]}" "$QA_RECEIPT" "$HOST:$REMOTE/incoming_${NAME}_qa.json"
"${SCP[@]}" "$TMP_CMD" "$HOST:$REMOTE/incoming_${NAME}.cmd"

STAGE_SENTINEL="STAGE_OK|$NAME|$MANIFEST_SHA|$BATCH_SHA"
STAGE_OUTPUT="$(remote_ps <<PS | tr -d '\r'
\$ErrorActionPreference = 'Stop'
function Assert-Hash([string]\$Path,[string]\$Expected,[string]\$Label) {
  if (-not (Test-Path -LiteralPath \$Path -PathType Leaf)) { throw (\$Label + ' missing') }
  \$actual = (Get-FileHash -Algorithm SHA256 -LiteralPath \$Path).Hash.ToLowerInvariant()
  if (\$actual -ne \$Expected) { throw (\$Label + ' hash mismatch: ' + \$actual) }
}
\$stage = '$REMOTE/datasets/.stage_$NAME'
try {
  Remove-Item -LiteralPath \$stage -Recurse -Force -ErrorAction SilentlyContinue
  New-Item -ItemType Directory -Path \$stage | Out-Null
  & tar.exe -xf '$REMOTE/incoming_$NAME.tar' -C \$stage
  if (\$LASTEXITCODE -ne 0) { throw 'dataset extraction failed' }
  \$incoming = Join-Path \$stage '$DATASET_BASE'
  Assert-Hash (Join-Path \$incoming 'manifest.jsonl') '$MANIFEST_SHA' 'dataset manifest'
  Assert-Hash (Join-Path \$incoming 'build_summary.json') '$SUMMARY_SHA' 'build summary'
  Assert-Hash (Join-Path \$incoming 'data.yaml') '$YAML_SHA' 'original data yaml'
  if (Test-Path -LiteralPath '$REMOTE_DATASET') { throw 'remote dataset target already exists' }
  Move-Item -LiteralPath \$incoming -Destination '$REMOTE_DATASET'
} finally {
  Remove-Item -LiteralPath \$stage -Recurse -Force -ErrorAction SilentlyContinue
  Remove-Item -LiteralPath '$REMOTE/incoming_$NAME.tar' -Force -ErrorAction SilentlyContinue
}
Assert-Hash '$REMOTE/incoming_${NAME}_model.pt' '$MODEL_SHA' 'model'
if (Test-Path -LiteralPath '$REMOTE_MODEL') {
  Assert-Hash '$REMOTE_MODEL' '$MODEL_SHA' 'existing model'
  Remove-Item -LiteralPath '$REMOTE/incoming_${NAME}_model.pt' -Force
} else {
  Move-Item -LiteralPath '$REMOTE/incoming_${NAME}_model.pt' -Destination '$REMOTE_MODEL'
}
Assert-Hash '$REMOTE/incoming_${NAME}_trainer.py' '$TRAINER_SHA' 'trainer'
if (Test-Path -LiteralPath '$REMOTE_TRAINER') {
  Assert-Hash '$REMOTE_TRAINER' '$TRAINER_SHA' 'existing trainer'
  Remove-Item -LiteralPath '$REMOTE/incoming_${NAME}_trainer.py' -Force
} else {
  Move-Item -LiteralPath '$REMOTE/incoming_${NAME}_trainer.py' -Destination '$REMOTE_TRAINER'
}
Assert-Hash '$REMOTE/incoming_${NAME}_preflight.py' '$PREFLIGHT_SHA' 'dataset preflight'
if (Test-Path -LiteralPath '$REMOTE_PREFLIGHT') {
  Assert-Hash '$REMOTE_PREFLIGHT' '$PREFLIGHT_SHA' 'existing dataset preflight'
  Remove-Item -LiteralPath '$REMOTE/incoming_${NAME}_preflight.py' -Force
} else {
  Move-Item -LiteralPath '$REMOTE/incoming_${NAME}_preflight.py' -Destination '$REMOTE_PREFLIGHT'
}
Assert-Hash '$REMOTE/incoming_${NAME}_prereg.json' '$PREREG_SHA' 'preregistration'
Move-Item -LiteralPath '$REMOTE/incoming_${NAME}_prereg.json' -Destination '$REMOTE_PREREG'
Assert-Hash '$REMOTE/incoming_${NAME}_build.json' '$BUILD_SHA' 'dataset build receipt'
Move-Item -LiteralPath '$REMOTE/incoming_${NAME}_build.json' -Destination '$REMOTE_BUILD'
Assert-Hash '$REMOTE/incoming_${NAME}_qa.json' '$QA_SHA' 'dataset QA receipt'
Move-Item -LiteralPath '$REMOTE/incoming_${NAME}_qa.json' -Destination '$REMOTE_QA'
Assert-Hash '$REMOTE/incoming_${NAME}.cmd' '$BATCH_SHA' 'launcher'
Move-Item -LiteralPath '$REMOTE/incoming_${NAME}.cmd' -Destination '$REMOTE_BATCH'
\$yaml = '$REMOTE_DATASET/data.yaml'
(Get-Content -LiteralPath \$yaml) -replace '^path:.*$', 'path: $REMOTE_DATASET' | Set-Content -LiteralPath \$yaml -Encoding ASCII
Assert-Hash '$REMOTE_DATASET/manifest.jsonl' '$MANIFEST_SHA' 'final dataset manifest'
if ('$STRICT_PREFLIGHT' -eq 'true') {
  & '$REMOTE_PY' '$REMOTE_PREFLIGHT' --dataset '$REMOTE_DATASET' --prereg '$REMOTE_PREREG' --verify-file-hashes --output '$REMOTE/experiments/$NAME/remote_dataset_preflight.json'
  if (\$LASTEXITCODE -ne 0) { throw 'remote full dataset preflight failed' }
}
Write-Output '$STAGE_SENTINEL'
PS
)"
printf '%s\n' "$STAGE_OUTPUT"
grep -Fqx "$STAGE_SENTINEL" <<<"$STAGE_OUTPUT" || die "remote staging returned no exact sentinel"
rm -f -- "$TMP_TAR" "$TMP_CMD"
TMP_TAR=""
TMP_CMD=""

say "start one detached WMI training job"
START="$(remote_ps <<PS | tr -d '\r'
\$ErrorActionPreference = 'Stop'
\$cmd = 'cmd.exe /d /c "C:\\fable\\launch_$NAME.cmd"'
\$r = Invoke-CimMethod -ClassName Win32_Process -MethodName Create -Arguments @{CommandLine=\$cmd}
if (\$r.ReturnValue -ne 0) { throw ('WMI create failed: ' + \$r.ReturnValue) }
Write-Output ('PID=' + \$r.ProcessId)
PS
)"
printf '%s\n' "$START"
[[ "$START" =~ PID=([0-9]+) ]] || die "WMI returned no PID"
printf '  run: %s\n  recipe: epochs=%s patience=%s batch=%s imgsz=%s\n  wait_for: %s\n  status: FABLE_3060_HOST=%q bash %q --status\n' \
  "$NAME" "$EPOCHS" "$PATIENCE" "$BATCH" "$IMGSZ" "${WAIT_FOR_RUN:-none}" "$HOST" "$0"
printf '\nStarted only. No holdout, promotion, deployment, ACTIVE or trading action occurred.\n'
