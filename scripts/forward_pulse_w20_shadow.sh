#!/usr/bin/env bash
# Shadow pulse for w20 midbox hardneg — never mainline.
# Does NOT write data/forward_log.csv, does NOT call executor, does NOT touch ACTIVE.
set -uo pipefail
cd "$(dirname "$0")/.."
mkdir -p logs data analysis/output
LOG=logs/forward_pulse_w20_shadow.log
exec >>"$LOG" 2>&1
echo "=== w20_shadow_pulse $(date -u +%Y-%m-%dT%H:%M:%SZ) ==="

PY="${PY:-.venv/bin/python}"
[ -x "$PY" ] || PY=python3
export PYTHONPATH=".:${HOME}/yoyo-trading:${PYTHONPATH:-}"
export YOYO_DATA_ROOT="${YOYO_DATA_ROOT:-$(pwd)}"

# Optional light refresh (skip offline)
if [ "${SKIP_UPDATE_OKX:-0}" != "1" ]; then
  echo "update_okx --swap-only --bar 15m"
  "$PY" -m src.data.update_okx --bar 15m --swap-only 2>&1 | tail -15 || echo "update_okx skipped/failed"
fi

echo "forward_shadow_w20_midbox --once"
"$PY" scripts/forward_shadow_w20_midbox.py --once \
  --weights analysis/output/w20_overnight/cycle_hardneg_c1/weights/best.pt \
  --conf 0.30 --window 24 2>&1 | tail -40

if [ -f analysis/output/w20_shadow_status.json ]; then
  "$PY" - <<'PY'
import json
from pathlib import Path
s=json.loads(Path("analysis/output/w20_shadow_status.json").read_text())
b=s.get("book",{})
g=s.get("gate",{})
print(f"shadow closed={g.get('closed')}/100 remaining={g.get('remaining')} "
      f"open={b.get('n_open')} mean_net_bp={b.get('mean_net_bp')} PF={b.get('profit_factor')}")
PY
fi
echo "=== done $(date -u +%Y-%m-%dT%H:%M:%SZ) ==="
