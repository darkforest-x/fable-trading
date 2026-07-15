#!/bin/bash
# 队列 #17：等 v6 晋升完成 → YOLO vs 规则 候选源 A/B（GPU，v6之后）
set -uo pipefail
cd "$(dirname "$0")/.."
exec >> logs/offline_queue17.log 2>&1
echo "=== queue17 start $(date) ==="
# 等 v6 两个底座都训完（owner_v6 都有 best.pt 且 GPU 空闲）
# 等 v6 训完 AND queue15 晋升完成（owner_best 指向 v6，避免抢到旧v5当候选源）
# 等 queue15 晋升完成（其日志出现 done 标记），用当时的 owner_best 跑 A/B
until grep -q "queue15 done" logs/offline_queue15.log 2>/dev/null; do sleep 120; done
echo "v6 训练完毕，启动 A/B $(date)"
bash scripts/ab_yolo_vs_rules.sh
echo "=== queue17 done $(date) ==="
