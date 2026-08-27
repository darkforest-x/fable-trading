#!/bin/bash
# Select the Owner-authorized 10,000-positive + 30,000-negative treatment.
#
# The shared launcher retains the frozen 960 recipe and all fail-closed
# holdout/promotion guards. This wrapper changes only immutable input identity,
# experiment/run names and the local receipt destination.
set -euo pipefail

cd "$(dirname "$0")/.."

export FABLE_T3_EXPERIMENT_ID="exp-15m-ma-launch-owner-yolo-neg30000-train960-v1"
export FABLE_T3_PREREG="experiments/active/${FABLE_T3_EXPERIMENT_ID}/preregistration.json"
export FABLE_T3_DATASET="datasets/ma_launch_owner_autofill10000_yolo_neg30000_v2"
export FABLE_T3_BUILD_RECEIPT="experiments/active/exp-15m-ma-launch-owner-yolo-dataset10000-neg30000-v2/results/dataset_build_receipt.json"
export FABLE_T3_QA_RECEIPT="experiments/active/exp-15m-ma-launch-owner-yolo-dataset10000-neg30000-v2/results/negative_expansion_audit.json"
export FABLE_T3_RUN_NAME="ma_launch_owner_yolo_neg30000_v2_y11s_ft960"
export FABLE_T3_IMGSZ="960"
export FABLE_T3_REMOTE_DATASET_NAME="ma_launch_owner_yolo_neg30000_v2_input"
export FABLE_T3_LOCAL_OUTPUT_ROOT="analysis/output/ma_launch_owner_yolo_neg30000_v2"
export FABLE_T3_DATASET_IMAGE_COUNT="40000"

exec bash scripts/train_15m_ma_launch_t3_on_3060.sh "$@"
