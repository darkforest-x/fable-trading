#!/bin/bash
# Ship v14 pad200 dataset (+ v12 base weights) to the LAN Windows RTX 3060
# over SSH/scp. Does NOT train, promote, or eval holdout.
#
# Division of labour (same as train_on_3060.sh / train_owner_hts.sh):
#   Mac  = source of truth (dataset build, eval, promote)
#   3060 = GPU only (C:/fable); wipe anytime
#
# Defaults (override with env — never put passwords in the repo):
#   FABLE_3060_HOST=zzc@192.168.1.5
#   FABLE_3060_REMOTE=C:/fable
#
# Usage (from repo root on Mac):
#   bash scripts/sync_v14_to_windows.sh
#   bash scripts/sync_v14_to_windows.sh --check   # SSH + remote paths only
#   FABLE_3060_HOST=zzc@other bash scripts/sync_v14_to_windows.sh
set -euo pipefail
cd "$(dirname "$0")/.."

HOST="${FABLE_3060_HOST:-zzc@192.168.1.5}"
REMOTE="${FABLE_3060_REMOTE:-C:/fable}"
SSH=(ssh -o BatchMode=yes -o ConnectTimeout=15)
SCP=(scp -o BatchMode=yes -q)
DATASET="datasets/dense_owner_v14_pad200"
BASE_CANDIDATES=(models/owner_v12_htip.pt models/owner_best.pt)
CHECK_ONLY=0

while [ $# -gt 0 ]; do
  case "$1" in
    --check) CHECK_ONLY=1; shift ;;
    --host) HOST="$2"; shift 2 ;;
    --remote) REMOTE="$2"; shift 2 ;;
    *) echo "unknown arg: $1" >&2; exit 2 ;;
  esac
done

say() { echo -e "\n\033[1;36m=== $* ===\033[0m"; }
die() { echo -e "\033[1;31m[X] $*\033[0m" >&2; exit 1; }

say "0) SSH ${HOST} -> ${REMOTE}"
"${SSH[@]}" "$HOST" "echo ok" >/dev/null 2>&1 || die "SSH unreachable: ${HOST} (LAN / ssh-agent / OpenSSH Server)"
"${SSH[@]}" "$HOST" "if (-not (Test-Path '${REMOTE}')) { throw 'missing remote root ${REMOTE}' }; Write-Output 'remote_ok'" \
  | tr -d '\r' | grep -q remote_ok || die "remote root missing: ${REMOTE}"

if [ "$CHECK_ONLY" = "1" ]; then
  echo "✅ SSH + remote root OK. Dataset sync skipped (--check)."
  exit 0
fi

[ -f "$DATASET/data.yaml" ] || die "数据集不存在: $DATASET/data.yaml（先看 analysis/p_v14_pad200_rebuild.md）"

BASE=""
for c in "${BASE_CANDIDATES[@]}"; do
  if [ -f "$c" ]; then BASE="$c"; break; fi
done
[ -n "$BASE" ] || die "缺少基座权重: ${BASE_CANDIDATES[*]}"

say "1) pack $DATASET (no .npy/.cache/AppleDouble)"
TAR=$(mktemp -t fable_v14).tar
COPYFILE_DISABLE=1 tar cf "$TAR" --exclude='*.npy' --exclude='*.cache' --exclude='._*' \
  -C datasets dense_owner_v14_pad200
echo "  pack: $(du -h "$TAR" | cut -f1)  base: $BASE"

say "2) scp → $HOST:$REMOTE"
"${SCP[@]}" "$TAR" "$HOST:$REMOTE/ds_v14.tar" || die "scp 数据集失败"
"${SCP[@]}" "$BASE" "$HOST:$REMOTE/models/$(basename "$BASE")" || {
  # models/ may be missing on a bare C:/fable — create then retry
  "${SSH[@]}" "$HOST" "New-Item -ItemType Directory -Force -Path '$REMOTE/models' | Out-Null"
  "${SCP[@]}" "$BASE" "$HOST:$REMOTE/models/$(basename "$BASE")" || die "scp 基座权重失败"
}
rm -f "$TAR"

say "3) remote unpack + count"
"${SSH[@]}" "$HOST" "
cd $REMOTE
Remove-Item -Recurse -Force datasets/dense_owner_v14_pad200 -ErrorAction SilentlyContinue
if (-not (Test-Path datasets)) { New-Item -ItemType Directory datasets | Out-Null }
tar xf ds_v14.tar -C datasets
Remove-Item ds_v14.tar -ErrorAction SilentlyContinue
Get-ChildItem -Path datasets\\dense_owner_v14_pad200 -Recurse -Force -Filter '._*' -ErrorAction SilentlyContinue | Remove-Item -Force
\$d = 'datasets\\dense_owner_v14_pad200'
if (-not (Test-Path \"\$d\\data.yaml\")) { throw 'data.yaml missing after unpack' }
foreach (\$s in @('train','val')) {
  '{0}: {1} images / {2} labels' -f \$s,
    (Get-ChildItem \"\$d\\images\\\$s\\*.png\" -ErrorAction SilentlyContinue).Count,
    (Get-ChildItem \"\$d\\labels\\\$s\\*.txt\" -ErrorAction SilentlyContinue).Count
}
" | tr -d '\r'

echo -e "\n\033[1;32m✅ synced. Next: start train on 3060 (see analysis/p_v14_windows_train.md)\033[0m"
echo "  Mac one-liner after sync (WMI, SSH-safe):"
echo "  ssh $HOST \"Invoke-CimMethod -ClassName Win32_Process -MethodName Create -Arguments @{CommandLine='cmd.exe /c cd /d C:\\\\fable && .venv\\\\Scripts\\\\python.exe -m src.detection.train --data datasets/dense_owner_v14_pad200/data.yaml --model models/$(basename "$BASE") --epochs 40 --patience 10 --batch 16 --workers 4 --device 0 --cache false --name owner_v14_pad200 > logs\\\\owner_v14_pad200.log 2>&1'}\""
echo "  (or on the box: .\\scripts\\train_v14_pad200_windows.ps1)"
echo "  NO promote from this script."
