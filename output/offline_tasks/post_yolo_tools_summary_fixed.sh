#!/usr/bin/env bash
set -euo pipefail

cd /Users/zhangzc/fable-trading-codex

log_path="${1:?log path required}"
target_log="${2:-output/offline_tasks/yolo_tools_20260709_220542.log}"

mkdir -p output/offline_tasks
exec > >(tee -a "$log_path") 2>&1

printf 'post-yolo summary fixed waiting: %s\n' "$(date)"
while ! grep -q "yolo tools task finished" "$target_log" 2>/dev/null; do
  printf 'still waiting for yolo tools task: %s\n' "$(date)"
  sleep 120
done

printf 'yolo tools task ended; summarizing: %s\n' "$(date)"
python3 - <<'PY'
import json
from pathlib import Path

report = Path("output/offline_tasks/yolo_tooling_eval_report.json")
out = Path("output/offline_tasks/yolo_tooling_eval_summary.md")
lines = ["# YOLO Tooling Eval Summary", ""]

if not report.exists():
    lines += [
        "Report not found.",
        "Check `output/offline_tasks/yolo_tools_20260709_220542.log`.",
    ]
else:
    data = json.loads(report.read_text())
    lines += [
        f"- dataset: `{data.get('dataset')}`",
        f"- weights: `{data.get('weights')}`",
        "",
    ]
    for step in data.get("steps", []):
        result = step.get("result", {})
        lines.append(f"## {step.get('name')}")
        lines.append(f"- ok: `{result.get('ok')}`")
        for key in [
            "samples",
            "sample_size",
            "gt_boxes",
            "pred_boxes",
            "matched_iou50",
            "recall_like_iou50",
            "pred_per_gt",
            "error",
        ]:
            if key in result:
                lines.append(f"- {key}: `{result[key]}`")
        lines.append("")

out.write_text("\n".join(lines))
print(out)
PY

printf 'post-yolo summary finished: %s\n' "$(date)"
printf 'output: output/offline_tasks/yolo_tooling_eval_summary.md\n'
