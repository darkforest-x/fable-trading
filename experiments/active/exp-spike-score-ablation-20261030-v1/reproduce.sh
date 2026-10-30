#!/bin/sh
set -eu
cd /Users/zhangzc/fable-trading
/Users/zhangzc/.local/bin/nautilus-python -m yoyo.evaluation.spike_score_ablation run --workers 4
/Users/zhangzc/.local/bin/nautilus-python -m yoyo.evaluation.spike_score_ablation summary
/Users/zhangzc/.local/bin/nautilus-python -m yoyo.evaluation.spike_score_ablation validate
