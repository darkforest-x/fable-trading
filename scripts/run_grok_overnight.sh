#!/bin/bash
# Grok 整晚：每任务用 --continue 累积多次调用直到完成（解决单轮预算不足）
GROK_DIR="$HOME/fable-trading-grok"
LOG="$HOME/fable-trading/logs/grok_overnight.log"
cd "$GROK_DIR"
exec >> "$LOG" 2>&1
echo "=== grok overnight start $(date) (continue-loop) ==="
G="grok --always-approve --permission-mode bypassPermissions --cwd $GROK_DIR --output-format plain"
for f in grok_tasks/tasks/task*.md; do
  tag=$(basename "$f" .md)
  echo "--- [$tag] 开始 $(date)"
  base_commits=$(git rev-list --count HEAD)
  $G --prompt-file "$f" 2>&1 | tail -8
  for i in $(seq 1 10); do
    now_commits=$(git rev-list --count HEAD)
    # 若本任务已产生新提交则视为完成
    if [ "$now_commits" -gt "$base_commits" ]; then echo "[$tag] 已提交,完成(轮$i)"; break; fi
    echo "[$tag] 继续 轮$i $(date)"
    $G -c "继续完成当前任务：写完代码、跑通、git add + commit + push origin grok/overnight。若任务确已完成并提交，只回复 TASK_DONE。若卡住无法继续，在 grok_tasks/RESULTS.md 记一行原因并回复 TASK_SKIP。" 2>&1 | tail -6
  done
  echo "--- [$tag] 结束 $(date)"
done
echo "=== grok overnight all done $(date) ==="
git log --oneline -15
