#!/bin/bash
# YOLO候选源 vs 规则候选源：同一套判断层+回测+验收闸门下正面PK（不动现有验证）
#
# Contention: do NOT run this while a YOLO train is on MPS/GPU — candidate
# scan is CPU/MPS heavy. Prefer train first, then A/B, or pause train.
set -euo pipefail
cd "$(dirname "$0")/.."
mkdir -p logs
exec >> logs/ab_yolo_vs_rules.log 2>&1
PY=.venv/bin/python
echo "=== A/B start $(date) ==="

if pgrep -f "src.detection.train" >/dev/null 2>&1; then
  echo "REFUSE: detection train is running — avoid parallel MPS contention" >&2
  exit 2
fi

echo "--- [1/3] 用 owner_best 生成 YOLO 候选数据集"
PYTHONPATH=. $PY scripts/yolo_candidate_source.py --weights models/owner_best.pt \
  --out data/judgment_yolo_swap.csv
test -s data/judgment_yolo_swap.csv

echo "--- [2/3] 两条路径各自训练+val评估（同一 judgment.train，同一切分纪律）"
$PY -m src.judgment.train --data data/judgment_yolo_swap.csv --tag ab_yolo | tail -40
RULE_CSV=$(ls -t data/swap_replication/swap_tp5_sl2.csv data/sweep_v3/judgment_v3_tp5_sl2.csv 2>/dev/null | head -1)
test -n "${RULE_CSV:-}" && test -s "$RULE_CSV"
$PY -m src.judgment.train --data "$RULE_CSV" --tag ab_rules | tail -40

echo "--- [3/3] 对比报告"
PYTHONPATH=. $PY - <<'PYEOF'
import json
import sys
from pathlib import Path

def load(tag):
    p = Path(f"analysis/output/{tag}_metrics.json")
    if not p.exists():
        raise SystemExit(f"missing metrics: {p}")
    return json.loads(p.read_text())

y, r = load("ab_yolo"), load("ab_rules")

def line(name, d):
    v = d.get("val", {})
    td = v.get("top_decile", {})
    return (
        f"{name}: 候选 {d.get('splits',{}).get('train',{}).get('n','?')}训 | "
        f"val AUC {v.get('roc_auc')} | p {d.get('val_permutation_p')} | "
        f"top净@0.2% {td.get('mean_net_ret')}"
    )

rep = [
    "# A/B: YOLO候选源 vs 规则候选源（同一判断层与验收闸门）\n",
    "**发现级 val 对比。判定：YOLO 的 top-decile 净收益 ≥ 规则 且 p<0.01 "
    "→ 赢得关键路径候选（下一步冻结+前向）；否则回侦察岗。**\n",
    "- " + line("YOLO候选", y),
    "- " + line("规则候选", r),
]
Path("analysis/p2a_yolo_critical_path_ab.md").write_text("\n".join(rep), encoding="utf-8")
print("\n".join(rep))
try:
    from src.notify import send
    send("⚔️ YOLO vs 规则 候选源PK完成:\n" + line("YOLO", y) + "\n" + line("规则", r) + "\n详见 p2a_yolo_critical_path_ab.md")
except Exception as exc:
    print("notify skipped:", exc, file=sys.stderr)
PYEOF
git add data/judgment_yolo_swap.csv analysis/output/ab_yolo_metrics.json analysis/output/ab_rules_metrics.json analysis/p2a_yolo_critical_path_ab.md 2>/dev/null || true
git commit -qm "A/B: YOLO candidate source vs rule scan (head-to-head, same gates)" && git push -q && echo pushed || true
echo "=== A/B done $(date) ==="
