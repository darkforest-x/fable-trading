#!/usr/bin/env bash
# Owner-approved expand: ~100 liquid USDT SWAP × 6m pre-holdout window.
# Wraps run_yolo_short_pool_chunked.sh. Do not pkill while
# analysis/output/SHORT_100_6M_PILOT.lock exists.
set -u
cd "$(dirname "$0")/.."

export CHUNK_SERIES="${CHUNK_SERIES:-1}"
export WEIGHTS="${WEIGHTS:-runs/detect/runs/detect/owner_side_short_tip_v1b/weights/best.pt}"
export OUT="${OUT:-data/judgment_yolo_owner_side_short_100_6m.csv}"
export SYMBOLS_FILE="${SYMBOLS_FILE:-analysis/output/yolo_short_100_6m_symbols.txt}"
export MONTHS="${MONTHS:-6}"
export END_BEFORE="${END_BEFORE:-2026-05-04}"
export LOG="${LOG:-analysis/output/yolo_owner_side_short_tip_v1b_100_6m_scan.log}"
export PIDFILE="${PIDFILE:-analysis/output/yolo_owner_side_short_tip_v1b_100_6m_scan.pid}"
export DEVICE="${DEVICE:-cpu}"

mkdir -p analysis/output data
cat > analysis/output/SHORT_100_6M_PILOT.lock <<EOF
owner=100_6m
wrapper_pid=$$
started=$(date -u +%Y-%m-%dT%H:%M:%SZ)
out=$OUT
window=[2025-11-04,2026-05-04)
symbols_file=$SYMBOLS_FILE
note=OWNER_APPROVED_ACTIVE — do NOT pkill yolo_candidate / do NOT bootstrap full-universe
EOF
# Block legacy guards that check SHORT_10 / SHORT_30 locks
cat > analysis/output/SHORT_10_PILOT.lock <<EOF
owner=superseded_by_100_6m
redirect=analysis/output/SHORT_100_6M_PILOT.lock
EOF
cat > analysis/output/SHORT_30_6M_PILOT.lock <<EOF
owner=superseded_by_100_6m
redirect=analysis/output/SHORT_100_6M_PILOT.lock
EOF

exec bash scripts/run_yolo_short_pool_chunked.sh
