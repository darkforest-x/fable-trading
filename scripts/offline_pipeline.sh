#!/bin/bash
# 离线接力管道：不依赖 Claude，额度耗尽也照常执行（owner 授意 2026-07-08 深夜）。
# 顺序：等合约拉取收尾 → 补漏重试 → 合约复制性检验 → 等 YOLO 训练结束 →
#       官方评估 → 若 mAP50<0.90 自动用 yolo11s 重训并再评 → 写 OFFLINE_RESULTS.md
# 启动：caffeinate -i nohup bash scripts/offline_pipeline.sh & （已由会话代启）
# 查看：tail -f logs/offline_run.log；结果汇总在仓库根 OFFLINE_RESULTS.md
set -uo pipefail
cd "$(dirname "$0")/.."
mkdir -p logs
exec >> logs/offline_run.log 2>&1
PY=.venv/bin/python

echo "=== offline pipeline start $(date) ==="

echo "--- [1/5] waiting for the swap fetch to finish..."
while pgrep -f "src.data.fetch_okx" >/dev/null; do sleep 120; done
echo "fetch process gone at $(date); mop-up pass for failed/partial symbols"
SWAPS=$(python3 -c "
from src.data.fetch_okx import DEFAULT_SYMBOLS
print(' '.join(sorted({s.replace('_USDT','_USDT_SWAP') if not s.endswith('_SWAP') else s for s in DEFAULT_SYMBOLS})))")
python3 -m src.data.fetch_okx --symbols $SWAPS
ls data/kline_fetched/okx_*_USDT_SWAP_15m_*.csv | wc -l | xargs echo "swap files ready:"

echo "--- [2/5] swap-universe replication test (val only)"
PYTHONPATH=. python3 scripts/swap_replication.py

echo "--- [3/5] waiting for YOLO training to finish..."
while pgrep -f "src.detection.train" >/dev/null; do sleep 300; done
echo "training done at $(date)"

echo "--- [4/5] official eval of dense_15m_full"
cp -n analysis/output/p2a_val_metrics.json analysis/output/p2a_val_metrics_smoke3.json 2>/dev/null || true
$PY -m src.detection.eval_visualize \
  --weights runs/detect/runs/detect/dense_15m_full/weights/best.pt --n-vis 5
MAP=$(python3 -c "import json; print(json.load(open('analysis/output/p2a_val_metrics.json'))['mAP50'])")
echo "dense_15m_full mAP50=$MAP"
if python3 -c "import sys; sys.exit(0 if float('$MAP') < 0.90 else 1)"; then
  echo "below 0.90 -> retraining with yolo11s (this takes hours)"
  caffeinate -i $PY -m src.detection.train --data datasets/dense_15m_full/data.yaml \
    --model yolo11s.pt --epochs 60 --patience 15 --name dense_15m_full_s
  $PY -m src.detection.eval_visualize \
    --weights runs/detect/runs/detect/dense_15m_full_s/weights/best.pt --n-vis 5
  echo "yolo11s mAP50=$(python3 -c "import json; print(json.load(open('analysis/output/p2a_val_metrics.json'))['mAP50'])")"
fi

echo "--- [5/5] writing OFFLINE_RESULTS.md"
python3 - <<'EOF'
import json
from datetime import datetime
from pathlib import Path

def load(p):
    p = Path(p)
    return json.loads(p.read_text()) if p.exists() else None

swap = load("analysis/output/swap_replication.json")
yolo = load("analysis/output/p2a_val_metrics.json")
lines = [
    "# 离线管道结果（自动生成，%s）" % datetime.now().strftime("%Y-%m-%d %H:%M"),
    "",
    "> 由 scripts/offline_pipeline.sh 无人值守产出。下个会话请先读这里，",
    "> 再按 HANDOFF.md 未决队列继续。运行日志：logs/offline_run.log",
    "",
    "## 合约复制性检验（val only，未碰 holdout）",
]
if swap:
    lines.append("")
    lines.append("| 配置 | 候选 | val AUC | p | top毛利 | 净@taker0.10%% | 净@maker0.06%% | maker成交率 |")
    lines.append("|---|---|---|---|---|---|---|---|")
    for r in swap:
        lines.append("| %s | %s | %.3f | %.3f | %.3f%%%% | %.3f%%%% | %.3f%%%% | %.0f%%%% |" % (
            r["config"], r["n_candidates"], r["val_auc"], r["perm_p"],
            100*r["top_gross"], 100*r["top_net_taker_010"],
            100*r["top_net_maker_006"], 100*r["maker_fill_rate"]))
else:
    lines.append("（未产出——查看日志）")
lines += ["", "## YOLO 全量训练官方评估", ""]
lines.append(json.dumps(yolo, indent=2) if yolo else "（未产出——查看日志）")
lines += ["", "判定提醒：mAP50 验收线 0.90；合约上 TP5/SL2 的 maker 净收益若与现货结论同向，复制性成立。"]
Path("OFFLINE_RESULTS.md").write_text("\n".join(lines), encoding="utf-8")
print("OFFLINE_RESULTS.md written")
EOF

echo "=== offline pipeline done $(date) ==="
