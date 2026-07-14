#!/bin/bash
# Grok 整晚：每个任务单独一次 grok 调用（单轮模式完成一个聚焦任务），循环串10个
# 隔离在 ~/fable-trading-grok worktree，不干扰主目录后台任务
GROK_DIR="$HOME/fable-trading-grok"
LOG="$HOME/fable-trading/logs/grok_overnight.log"
cd "$GROK_DIR"
exec >> "$LOG" 2>&1
echo "=== grok overnight start $(date) (per-task loop) ==="
for f in grok_tasks/tasks/task*.md; do
  echo "--- [$f] 开始 $(date)"
  grok --always-approve --permission-mode acceptEdits --cwd "$GROK_DIR" \
       --check --output-format plain --prompt-file "$f" 2>&1 | tail -40
  echo "--- [$f] 结束 $(date)"
done
echo "=== grok overnight all-tasks done $(date) ==="
git log --oneline -12
