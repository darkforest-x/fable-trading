#!/bin/sh
set -eu
cd "$(git rev-parse --show-toplevel)"
PY=/Users/zhangzc/.local/bin/nautilus-python
$PY -m yoyo.evaluation.v3_causal_close_entry run --workers 4
$PY -m yoyo.evaluation.v3_causal_close_entry summary
$PY -m yoyo.evaluation.v3_causal_close_entry validate
