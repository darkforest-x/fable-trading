#!/bin/bash
# Train the outcome-labelled 5m dataset at imgsz=960 for all 40 epochs.
#
# Labels here are barrier outcomes: take-profit is positive, stop-out is
# negative. Both classes therefore contain a launch already underway, so the
# "did the last two bars move" shortcut that produced mAP 0.91 on the 15m set
# is worthless and the detector has to read the compression itself.
#
# One render per pattern rather than eight, so 3,835 images stand for 3,835
# distinct patterns instead of an eighth that many repeated.
set -euo pipefail

cd "$(dirname "$0")/.."

export FABLE_T3_EXPERIMENT_ID="exp-5m-ma-launch-outcome-train960-v1"
export FABLE_T3_PREREG="experiments/active/${FABLE_T3_EXPERIMENT_ID}/preregistration.json"
export FABLE_T3_DATASET="datasets/ma_launch_5m_outcome_v1"
export FABLE_T3_BUILD_RECEIPT="experiments/active/${FABLE_T3_EXPERIMENT_ID}/results/dataset_build_receipt.json"
export FABLE_T3_QA_RECEIPT="experiments/active/${FABLE_T3_EXPERIMENT_ID}/results/dataset_qa_receipt.json"
export FABLE_T3_RUN_NAME="ma_launch_5m_outcome_v1_y11s_ft960"
export FABLE_T3_IMGSZ="960"
export FABLE_T3_EPOCHS="40"
export FABLE_T3_PATIENCE="0"
export FABLE_T3_BATCH="8"
export FABLE_T3_REMOTE_DATASET_NAME="ma_launch_5m_outcome_v1_input"
export FABLE_T3_LOCAL_OUTPUT_ROOT="analysis/output/ma_launch_5m_outcome_v1"
export FABLE_T3_DATASET_IMAGE_COUNT="3812"
export FABLE_T3_STRICT_PREFLIGHT="true"

exec bash scripts/train_15m_ma_launch_t3_on_3060.sh "$@"
