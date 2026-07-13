#!/bin/bash
# 视觉侦察循环：每 30 分钟扫一次全部合约右缘（先增量拉数据再扫）
# 启动: nohup bash scripts/scout_loop.sh & | 停止: pkill -f scout_loop
cd "$(dirname "$0")/.."
while true; do
  python3 -m src.data.update_okx >> logs/scout_loop.log 2>&1 || true
  PYTHONPATH=. .venv/bin/python scripts/visual_scout.py >> logs/scout_loop.log 2>&1 || true
  rsync -az src/webapp/static/scout.html src/webapp/static/scout root@103.214.174.58:/opt/fable-trading/src/webapp/static/ >> logs/scout_loop.log 2>&1 || true
  sleep 1800
done
