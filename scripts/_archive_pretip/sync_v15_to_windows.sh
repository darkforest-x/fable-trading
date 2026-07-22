#!/bin/bash
# Ship v15 tipval (val-only pad200) to Windows RTX 3060.
# Reuses already-synced dense_owner_v14_pad200 TRAIN via relative data.yaml path.
# Does NOT train, promote, or eval holdout.
#
#   bash scripts/sync_v15_to_windows.sh
#   bash scripts/sync_v15_to_windows.sh --check
set -euo pipefail
cd "$(dirname "$0")/.."

HOST="${FABLE_3060_HOST:-zzc@192.168.1.3}"
REMOTE="${FABLE_3060_REMOTE:-C:/fable}"
SSH=(ssh -o BatchMode=yes -o ConnectTimeout=15)
SCP=(scp -o BatchMode=yes -q)
DATASET="datasets/dense_owner_v15_tipval"
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
"${SSH[@]}" "$HOST" "echo ok" >/dev/null 2>&1 || die "SSH unreachable: ${HOST}"
"${SSH[@]}" "$HOST" "if (-not (Test-Path '${REMOTE}')) { throw 'missing' }; Write-Output remote_ok" \
  | tr -d '\r' | grep -q remote_ok || die "remote root missing"

if [ "$CHECK_ONLY" = "1" ]; then
  echo "✅ SSH OK"
  exit 0
fi

[ -f "$DATASET/data.yaml" ] || die "missing $DATASET/data.yaml"
[ -f "$DATASET/tipval_summary.json" ] || die "missing tipval_summary.json — finish build first"

BASE=""
for c in "${BASE_CANDIDATES[@]}"; do
  if [ -f "$c" ]; then BASE="$c"; break; fi
done
[ -n "$BASE" ] || die "missing base weights"

# Ensure v14 train already on Windows (yaml points at it)
"${SSH[@]}" "$HOST" "
if (-not (Test-Path '${REMOTE}/datasets/dense_owner_v14_pad200/images/train')) {
  throw 'v14 train missing on Windows — run sync_v14_to_windows.sh first'
}
Write-Output v14_train_ok
" | tr -d '\r' | grep -q v14_train_ok || die "v14 train not on Windows"

say "1) pack val-only tipval (no train images)"
TAR=$(mktemp -t fable_v15).tar
COPYFILE_DISABLE=1 tar cf "$TAR" --exclude='*.npy' --exclude='*.cache' --exclude='._*' \
  --exclude='images/train' --exclude='labels/train' \
  -C datasets dense_owner_v15_tipval
echo "  pack: $(du -h "$TAR" | cut -f1)  base: $BASE"

say "2) scp → $HOST:$REMOTE"
"${SSH[@]}" "$HOST" "New-Item -ItemType Directory -Force -Path '$REMOTE/models','$REMOTE/logs','$REMOTE/datasets' | Out-Null"
"${SCP[@]}" "$TAR" "$HOST:$REMOTE/ds_v15.tar" || die "scp failed"
"${SCP[@]}" "$BASE" "$HOST:$REMOTE/models/$(basename "$BASE")" || die "scp base failed"
# Windows train entrypoint (same as v14)
if [ -f train_dense.py ]; then
  "${SCP[@]}" train_dense.py "$HOST:$REMOTE/train_dense.py" || true
fi
rm -f "$TAR"

say "3) remote unpack + junction train from v14 + rewrite data.yaml"
"${SSH[@]}" "$HOST" "
cd $REMOTE
Remove-Item -Recurse -Force datasets/dense_owner_v15_tipval -ErrorAction SilentlyContinue
if (-not (Test-Path datasets)) { New-Item -ItemType Directory datasets | Out-Null }
tar xf ds_v15.tar -C datasets
Remove-Item ds_v15.tar -ErrorAction SilentlyContinue
Get-ChildItem -Path datasets\\dense_owner_v15_tipval -Recurse -Force -Filter '._*' -ErrorAction SilentlyContinue | Remove-Item -Force
\$d = 'datasets\\dense_owner_v15_tipval'
\$v14t = 'datasets\\dense_owner_v14_pad200\\images\\train'
\$v14l = 'datasets\\dense_owner_v14_pad200\\labels\\train'
if (-not (Test-Path \$v14t)) { throw 'v14 train missing' }
# train_dense.py counts images/train under dataset root — junction, do not copy
Remove-Item -Recurse -Force \"\$d\\images\\train\",\"\$d\\labels\\train\" -ErrorAction SilentlyContinue
New-Item -ItemType Directory -Force -Path \"\$d\\images\",\"\$d\\labels\" | Out-Null
cmd /c mklink /J \"\$d\\images\\train\" \"\$((Resolve-Path \$v14t).Path)\"
cmd /c mklink /J \"\$d\\labels\\train\" \"\$((Resolve-Path \$v14l).Path)\"
@'
path: C:/fable/datasets/dense_owner_v15_tipval
train: images/train
val: images/val
names:
  0: dense_cluster
'@ | Set-Content -Encoding ascii \"\$d\\data.yaml\"
'val: {0} images / {1} labels' -f,
  (Get-ChildItem \"\$d\\images\\val\\*.png\" -ErrorAction SilentlyContinue).Count,
  (Get-ChildItem \"\$d\\labels\\val\\*.txt\" -ErrorAction SilentlyContinue).Count
'train via junction: {0} images' -f (Get-ChildItem \"\$d\\images\\train\\*.png\").Count
" | tr -d '\r'

echo -e "\n\033[1;32m✅ synced. Start train:\033[0m"
echo "  bash scripts/v15_train_start.sh"
echo "  NO promote."
