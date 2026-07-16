#!/bin/bash
# 视觉侦察循环（低延迟版 2026-07-15）
# - 热路径：只跑 visual_scout（内部 live 拉 top-N 币种右缘 K 线）
# - 冷路径：每 N 轮才全量 update_okx（不阻塞推送）
# 启动: nohup bash scripts/scout_loop.sh >> logs/scout_loop.log 2>&1 &
# 停止: 找到 pid 后 kill（避免 pkill -f 误伤）
cd "$(dirname "$0")/.."
mkdir -p logs
export PYTHONPATH=.
export SCOUT_MAX_AGE_BARS="${SCOUT_MAX_AGE_BARS:-1}"   # 只报最近 1 根 15m
export SCOUT_TOP_N="${SCOUT_TOP_N:-60}"
export SCOUT_CONF="${SCOUT_CONF:-0.25}"
SLEEP_SEC="${SCOUT_SLEEP_SEC:-120}"                     # 轮询间隔 2 分钟
UPDATE_EVERY="${SCOUT_UPDATE_EVERY:-15}"             # 约 30 分钟做一次全量增量更新
n=0
echo "=== scout_loop start $(date) sleep=${SLEEP_SEC}s max_age_bars=${SCOUT_MAX_AGE_BARS} top_n=${SCOUT_TOP_N} ==="
while true; do
  n=$((n + 1))
  echo "--- scout cycle $n $(date) ---"
  # Hot path first: detect ASAP
  .venv/bin/python scripts/visual_scout.py || true
  # Push gallery to VPS (best-effort, non-blocking timeout)
  rsync -az --timeout=20 \
    src/webapp/static/scout.html src/webapp/static/scout \
    root@103.214.174.58:/opt/fable-trading/src/webapp/static/ 2>/dev/null || true
  # Cold path: full kline tip update occasionally (for other jobs / cache)
  if [ $((n % UPDATE_EVERY)) -eq 0 ]; then
    echo "--- cold update_okx $(date) ---"
    python3 -m src.data.update_okx || true
  fi
  sleep "$SLEEP_SEC"
done
