#!/bin/bash
# Grok 无头整晚批次（--prompt-file 避免长prompt被shell破坏；grok/overnight 分支隔离）
cd "$(dirname "$0")/.."
git checkout grok/overnight 2>/dev/null || git checkout -b grok/overnight
exec >> logs/grok_overnight.log 2>&1
echo "=== grok overnight start $(date) ==="
grok --always-approve --permission-mode acceptEdits \
     --output-format plain --prompt-file grok_tasks/_driver_prompt.md
echo "=== grok overnight exit $(date) ==="
