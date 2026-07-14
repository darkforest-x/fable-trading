#!/bin/bash
# Grok 无头整晚批次：独立 worktree(~/fable-trading-grok)隔离工作文件，不干扰主目录后台任务
GROK_DIR="$HOME/fable-trading-grok"
cd "$GROK_DIR"
exec >> "$HOME/fable-trading/logs/grok_overnight.log" 2>&1
echo "=== grok overnight start $(date) (worktree isolated) ==="
grok --always-approve --permission-mode acceptEdits --cwd "$GROK_DIR" \
     --output-format plain --prompt-file grok_tasks/_driver_prompt.md
echo "=== grok overnight exit $(date) ==="
