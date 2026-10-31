#!/bin/sh
# Frozen study; completed scans and native positions are reused.
set -eu
cd /Users/zhangzc/fable-trading
export OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1
HARMONIC_PY=/Users/zhangzc/.local/share/crypto-toolkit/venvs/pyharmonics/bin/python
NATIVE_PY=/Users/zhangzc/.local/share/crypto-toolkit/venvs/nautilus/bin/python
STUDY=experiments/active/exp-monday-harmonic-confluence-20261031-v1
"$HARMONIC_PY" -m yoyo.evaluation.monday_harmonic scan --workers 6
"$HARMONIC_PY" -m yoyo.evaluation.monday_harmonic_summary validate
"$NATIVE_PY" -m yoyo.evaluation.monday_harmonic controls --workers 4
"$HARMONIC_PY" -m yoyo.evaluation.monday_harmonic_summary audit
if [ ! -f "$STUDY/summary.json" ]; then
  "$HARMONIC_PY" -m yoyo.evaluation.monday_harmonic_summary summary
fi
if [ ! -f "$STUDY/allin_monthly.json" ]; then
  "$HARMONIC_PY" -m yoyo.evaluation.monday_harmonic_summary monthly
fi
