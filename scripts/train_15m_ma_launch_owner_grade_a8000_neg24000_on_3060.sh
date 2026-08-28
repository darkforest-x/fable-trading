#!/bin/bash
# Select the Owner-authorized Grade-A 8,000-positive + matched 24,000-negative run.
#
# The shared launcher owns local/remote full-file preflight, dependency and
# content hashes, CUDA/NMS checks, detached execution and fail-closed receipts.
# This wrapper changes only the immutable experiment and output identities.
set -euo pipefail

cd "$(dirname "$0")/.."

export FABLE_T3_EXPERIMENT_ID="exp-15m-ma-launch-owner-grade-a8000-neg24000-train960-v1"
export FABLE_T3_PREREG="experiments/active/${FABLE_T3_EXPERIMENT_ID}/preregistration.json"
export FABLE_T3_DATASET="datasets/ma_launch_owner_grade_a8000_yolo_neg24000_v1"
export FABLE_T3_BUILD_RECEIPT="experiments/active/exp-15m-ma-launch-owner-grade-a8000-neg24000-v1/results/dataset_build_receipt.json"
export FABLE_T3_QA_RECEIPT="experiments/active/exp-15m-ma-launch-owner-grade-a8000-neg24000-v1/results/independent_qa_receipt.json"
export FABLE_T3_RUN_NAME="ma_launch_owner_grade_a8000_neg24000_v1_y11s_ft960"
export FABLE_T3_IMGSZ="960"
export FABLE_T3_REMOTE_DATASET_NAME="ma_launch_owner_grade_a8000_neg24000_v1_input"
export FABLE_T3_LOCAL_OUTPUT_ROOT="analysis/output/ma_launch_owner_grade_a8000_neg24000_v1"
export FABLE_T3_DATASET_IMAGE_COUNT="32000"
export FABLE_T3_STRICT_PREFLIGHT="true"

exec bash scripts/train_15m_ma_launch_t3_on_3060.sh "$@"
