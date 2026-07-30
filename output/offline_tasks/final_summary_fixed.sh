#!/usr/bin/env bash
set -euo pipefail

cd /Users/zhangzc/fable-trading-codex

log_path="${1:?log path required}"
post_yolo_log="${2:?post-yolo summary log required}"

mkdir -p output/offline_tasks
exec > >(tee -a "$log_path") 2>&1

printf 'final summary fixed waiting: %s\n' "$(date)"

expand_log="output/offline_tasks/expand_swap_15m_fixed_20260709_220053.log"
yolo_log="output/offline_tasks/yolo_tools_20260709_220542.log"
data_audit_log="output/offline_tasks/post_expand_data_audit_20260709_220937.log"

while true; do
  missing=""
  grep -q "expand swap fixed finished" "$expand_log" 2>/dev/null || missing="$missing expand"
  grep -q "yolo tools task finished" "$yolo_log" 2>/dev/null || missing="$missing yolo_tools"
  grep -q "post-expand data audit finished" "$data_audit_log" 2>/dev/null || missing="$missing data_audit"
  grep -q "post-yolo summary finished" "$post_yolo_log" 2>/dev/null || missing="$missing yolo_summary"
  if [ -z "$missing" ]; then
    break
  fi
  printf 'waiting for:%s at %s\n' "$missing" "$(date)"
  sleep 180
done

printf 'all finished markers seen: %s\n' "$(date)"
python3 - <<'PY'
import json
from pathlib import Path

out = Path("output/offline_tasks/FINAL_OFFLINE_SUMMARY_CORRECTED.md")
lines = [
    "# Final Offline Summary",
    "",
    "Generated after finished markers appeared in all watched logs.",
    "",
]

for path in [
    "output/offline_tasks/okx_swap_universe_summary.json",
    "output/offline_tasks/data_audit_after_expand_summary.json",
]:
    current = Path(path)
    lines.append(f"## {path}")
    if current.exists():
        try:
            data = json.loads(current.read_text())
            for key, value in data.items():
                lines.append(f"- {key}: `{value}`")
        except Exception as exc:
            lines.append(f"- parse_error: `{exc!r}`")
    else:
        lines.append("- missing")
    lines.append("")

yolo_summary = Path("output/offline_tasks/yolo_tooling_eval_summary.md")
lines.append("## YOLO tooling summary")
lines.append(yolo_summary.read_text() if yolo_summary.exists() else "missing")
lines.append("")

lines.append("## Current SWAP 15m file count")
count = len(list(Path("data/kline_fetched").glob("okx_*_USDT_SWAP_15m_*.csv")))
lines.append(f"- count: `{count}`")
lines.append("")

lines.append("## Files to inspect")
for path in [
    "output/offline_tasks/data_audit_after_expand.csv",
    "output/offline_tasks/yolo_tooling_eval_report.json",
    "output/offline_tasks/yolo_other_model_task_pack.md",
    "output/offline_tasks/fable_gap_and_offline_plan.md",
]:
    lines.append(f"- `{path}`")

lines.append("")
lines.append("## Next manual inputs needed")
lines.append("- Owner/model label findings CSV from `output/offline_tasks/yolo_other_model_task_pack.md`.")
lines.append("- Owner approval before any auto_label threshold changes or YOLO retraining.")
lines.append("- Owner approval before adding forward tracking to daily scheduler.")

out.write_text("\n".join(lines))
print(out)
PY

printf 'final summary fixed finished: %s\n' "$(date)"
printf 'output: output/offline_tasks/FINAL_OFFLINE_SUMMARY_CORRECTED.md\n'
