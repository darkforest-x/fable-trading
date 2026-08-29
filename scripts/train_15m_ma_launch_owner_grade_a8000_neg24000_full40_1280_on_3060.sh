#!/bin/bash
# Train the same frozen Owner Grade-A dataset at imgsz=1280 for all 40 epochs.
# The remote job waits for the paired 960 run to finish successfully, so the
# two arms never contend for the single RTX 3060.
set -euo pipefail

cd "$(dirname "$0")/.."

export FABLE_T3_EXPERIMENT_ID="exp-15m-ma-launch-owner-grade-a8000-neg24000-train1280-full40-v1"
export FABLE_T3_PREREG="experiments/active/${FABLE_T3_EXPERIMENT_ID}/preregistration.json"
export FABLE_T3_DATASET="datasets/ma_launch_owner_grade_a8000_yolo_neg24000_v1"
export FABLE_T3_BUILD_RECEIPT="experiments/active/exp-15m-ma-launch-owner-grade-a8000-neg24000-v1/results/dataset_build_receipt.json"
export FABLE_T3_QA_RECEIPT="experiments/active/exp-15m-ma-launch-owner-grade-a8000-neg24000-v1/results/independent_qa_receipt.json"
export FABLE_T3_RUN_NAME="ma_launch_owner_grade_a8000_neg24000_v1_y11s_ft1280_full40"
export FABLE_T3_IMGSZ="1280"
export FABLE_T3_EPOCHS="40"
export FABLE_T3_PATIENCE="0"
export FABLE_T3_BATCH="8"
export FABLE_T3_WAIT_FOR_RUN="ma_launch_owner_grade_a8000_neg24000_v1_y11s_ft960_full40"
export FABLE_T3_REMOTE_DATASET_NAME="ma_launch_owner_grade_a8000_neg24000_v1_full40_1280_input"
export FABLE_T3_LOCAL_OUTPUT_ROOT="analysis/output/ma_launch_owner_grade_a8000_neg24000_v1"
export FABLE_T3_DATASET_IMAGE_COUNT="32000"
export FABLE_T3_STRICT_PREFLIGHT="true"

exec bash scripts/train_15m_ma_launch_t3_on_3060.sh "$@"
