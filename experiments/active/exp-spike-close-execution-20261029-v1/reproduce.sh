#!/bin/sh
# Run from the repository root. Existing done/audit receipts prevent replays.
set -eu
runner=/Users/zhangzc/.local/bin/nautilus-python
"$runner" -m pytest -q tests/evaluation/test_spike_close_execution.py tests/evaluation/test_allin3_close_hybrid.py
"$runner" -m yoyo.evaluation.spike_close_execution run --workers 4
"$runner" -m yoyo.evaluation.spike_close_execution summary
"$runner" -m yoyo.evaluation.spike_close_execution validate
