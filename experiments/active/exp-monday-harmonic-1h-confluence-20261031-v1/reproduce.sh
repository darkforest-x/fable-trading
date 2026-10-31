#!/bin/sh
# Independent timeframe arm; all existing scans and exact native fills are reused.
set -eu
cd /Users/zhangzc/fable-trading
export OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1
HARMONIC_PY=/Users/zhangzc/.local/share/crypto-toolkit/venvs/pyharmonics/bin/python
NATIVE_PY=/Users/zhangzc/.local/share/crypto-toolkit/venvs/nautilus/bin/python
STUDY=experiments/active/exp-monday-harmonic-1h-confluence-20261031-v1
CONFIG="$STUDY/config.json"
"$HARMONIC_PY" -m yoyo.evaluation.monday_harmonic scan --workers 3 --config "$CONFIG"
"$HARMONIC_PY" -m yoyo.evaluation.monday_harmonic_summary validate --config "$CONFIG"
"$NATIVE_PY" -m yoyo.evaluation.monday_harmonic controls --workers 4 --config "$CONFIG"
"$HARMONIC_PY" -m yoyo.evaluation.monday_harmonic_summary audit --config "$CONFIG"
if [ ! -f "$STUDY/summary.json" ]; then
  "$HARMONIC_PY" -m yoyo.evaluation.monday_harmonic_summary summary --config "$CONFIG"
fi
if [ ! -f "$STUDY/allin_monthly.json" ]; then
  "$HARMONIC_PY" -m yoyo.evaluation.monday_harmonic_summary monthly --config "$CONFIG"
fi
