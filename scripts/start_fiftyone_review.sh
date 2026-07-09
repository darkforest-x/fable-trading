#!/bin/bash
# Launch FiftyOne App with GT + model preds for dense_15m_full val.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

FO_PY="${ROOT}/.venv_yolo_tools/bin/python"
if [ ! -x "$FO_PY" ]; then
  FO_PY="/Users/zhangzc/fable-trading-codex/.venv_yolo_tools/bin/python"
fi
if [ ! -x "$FO_PY" ]; then
  echo "ERROR: fiftyone venv not found. Create with: python3 -m venv .venv_yolo_tools && .venv_yolo_tools/bin/pip install fiftyone fiftyone-brain"
  exit 1
fi

# FiftyOne YOLOv5 importer expects dataset.yaml
if [ -f datasets/dense_15m_full/data.yaml ] && [ ! -e datasets/dense_15m_full/dataset.yaml ]; then
  ln -sf data.yaml datasets/dense_15m_full/dataset.yaml
fi

PRED_DIR="datasets/dense_15m_full/preds_val_conf30"
if [ ! -f "${PRED_DIR}/pred_meta.json" ]; then
  echo "preds missing — exporting with project .venv (may take a few minutes)..."
  .venv/bin/python scripts/export_yolo_preds_for_audit.py \
    --dataset datasets/dense_15m_full --split val --conf 0.30
fi

mkdir -p output/offline_tasks
cat > output/offline_tasks/FIFTYONE_ACCESS.md <<EOF
# FiftyOne 本地访问

- URL: http://127.0.0.1:5151
- Dataset: fable_dense_val
- Fields: ground_truth (规则 E2.1 标签), predictions (旧 best.pt conf0.30)
- 用法: 左侧打开两个 label field 对比；有 mistakenness 则按该字段排序

启动命令:
  bash scripts/start_fiftyone_review.sh

生成: $(date)
EOF

echo "Starting FiftyOne App on http://127.0.0.1:5151 ..."
exec "$FO_PY" scripts/fiftyone_label_audit.py \
  --dataset datasets/dense_15m_full \
  --split val \
  --preds "${PRED_DIR}" \
  --export-hard output/offline_tasks/fiftyone_hard \
  --launch --port 5151 --address 127.0.0.1
