#!/bin/bash
# Ship v16 tipuni (unified-pipeline dataset) + yolo11n base to Windows RTX 3060.
# Full dataset (own train+val; no junction — v16 negatives differ from v14's).
# Does NOT train, promote, or eval holdout.
#
#   bash scripts/sync_v16_to_windows.sh
#   bash scripts/sync_v16_to_windows.sh --check
set -euo pipefail
cd "$(dirname "$0")/.."

HOST="${FABLE_3060_HOST:-zzc@192.168.1.3}"
REMOTE="${FABLE_3060_REMOTE:-C:/fable}"
SSH=(ssh -o BatchMode=yes -o ConnectTimeout=15)
SCP=(scp -o BatchMode=yes -q)
DATASET="datasets/dense_owner_v16_tipuni"
# Owner ruling 2026-07-23: cold start ONLY — v12-lineage never a training base
# (analysis/p_v15_dataset_confound.md). yolo11n primary, yolo11s backup.
BASES=(models/yolo11n.pt models/yolo11s.pt)
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

if [ "$CHECK_ONLY" = "1" ]; then
  echo "✅ SSH OK"
  exit 0
fi

[ -f "$DATASET/data.yaml" ] || die "missing $DATASET/data.yaml"
[ -f "$DATASET/v16_meta.json" ] || die "missing v16_meta.json — finish build first"
for b in "${BASES[@]}"; do [ -f "$b" ] || die "missing base $b"; done

say "1) pack full v16 dataset"
TAR=$(mktemp -t fable_v16).tar
COPYFILE_DISABLE=1 tar cf "$TAR" --exclude='*.npy' --exclude='*.cache' --exclude='._*' \
  -C datasets dense_owner_v16_tipuni
echo "  pack: $(du -h "$TAR" | cut -f1)"

say "2) scp → $HOST:$REMOTE"
"${SSH[@]}" "$HOST" "New-Item -ItemType Directory -Force -Path '$REMOTE/models','$REMOTE/logs','$REMOTE/datasets' | Out-Null"
"${SCP[@]}" "$TAR" "$HOST:$REMOTE/ds_v16.tar" || die "scp failed"
for b in "${BASES[@]}"; do
  "${SCP[@]}" "$b" "$HOST:$REMOTE/models/$(basename "$b")" || die "scp base failed"
done
rm -f "$TAR"

say "3) remote unpack + local data.yaml"
"${SSH[@]}" "$HOST" "
cd $REMOTE
Remove-Item -Recurse -Force datasets/dense_owner_v16_tipuni -ErrorAction SilentlyContinue
tar xf ds_v16.tar -C datasets
Remove-Item ds_v16.tar -ErrorAction SilentlyContinue
Get-ChildItem -Path datasets\\dense_owner_v16_tipuni -Recurse -Force -Filter '._*' -ErrorAction SilentlyContinue | Remove-Item -Force
\$d = 'datasets\\dense_owner_v16_tipuni'
'train: {0} images / {1} labels' -f,
  (Get-ChildItem \"\$d\\images\\train\\*.png\").Count,
  (Get-ChildItem \"\$d\\labels\\train\\*.txt\").Count
'val: {0} images / {1} labels' -f,
  (Get-ChildItem \"\$d\\images\\val\\*.png\").Count,
  (Get-ChildItem \"\$d\\labels\\val\\*.txt\").Count
" | tr -d '\r'

echo -e "\n\033[1;32m✅ synced. Start train:\033[0m"
echo "  bash scripts/v16_train_start.sh"
echo "  NO promote."
