#!/bin/bash
# Run the Owner-authorized single-variable 15m t-3 imgsz=1280 experiment.
#
# This wrapper only selects the committed treatment contract. The shared
# launcher still owns dependency, hash, CUDA, WMI, holdout and promotion gates.
set -euo pipefail

cd "$(dirname "$0")/.."

export FABLE_T3_EXPERIMENT_ID="exp-15m-ma-launch-t3-yolo10000-imgsz1280-v1"
export FABLE_T3_PREREG="experiments/active/exp-15m-ma-launch-t3-yolo10000-imgsz1280-v1/preregistration.json"
export FABLE_T3_RUN_NAME="ma_launch_t3_10000_v1_y11s_ft_imgsz1280"
export FABLE_T3_IMGSZ="1280"
export FABLE_T3_REMOTE_DATASET_NAME="ma_launch_t3_10000_v1_imgsz1280_input"

exec bash scripts/train_15m_ma_launch_t3_on_3060.sh "$@"
