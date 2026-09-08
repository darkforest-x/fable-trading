#!/bin/bash
# Recompute from the frozen local snapshots. Do not overwrite the delivered study.
# Public API retention/revisions mean fetching today cannot promise original bytes.
# Original acquisition calls, dates and hashes are in collection_receipt.json,
# history manifests and derivatives4h/{manifest,coverage}.json.
set -euo pipefail
cd /Users/zhangzc/fable-trading
SPIKE_OUTPUT_ROOT="${SPIKE_OUTPUT_ROOT:?Set a new output directory outside the delivered results}"
SPIKE_EXPOSURE_ROUND="${SPIKE_EXPOSURE_ROUND:?Record this additional audit exposure in the ledger first}"
test ! -e "$SPIKE_OUTPUT_ROOT"
mkdir -p "$SPIKE_OUTPUT_ROOT"

.venv/bin/python -m pytest -q \
  tests/test_altcoin_features.py tests/test_altcoin_history.py \
  tests/test_altcoin_derivatives.py tests/test_altcoin_accounting.py \
  tests/test_altcoin_trend_research.py tests/test_altcoin_trend_figures.py \
  tests/test_altcoin_cost_diagnostics.py tests/test_altcoin_trend_report.py

.venv/bin/python -m yoyo.evaluation.altcoin_trend_research \
  --history data/altcoin_trends_20260909_v1/history_development/manifest.json \
  --phase development --periods 60,240 --out-dir "$SPIKE_OUTPUT_ROOT/development"
.venv/bin/python -m yoyo.evaluation.altcoin_trend_research \
  --history data/altcoin_trends_20260909_v1/history_full_verified/manifest.json \
  --phase audit --periods 60,240 --selection "$SPIKE_OUTPUT_ROOT/development" \
  --derivatives data/altcoin_trends_20260909_v1/derivatives4h \
  --exposure-round "$SPIKE_EXPOSURE_ROUND" --out-dir "$SPIKE_OUTPUT_ROOT/audit"
.venv/bin/python -m yoyo.evaluation.altcoin_trend_research \
  --history data/altcoin_trends_20260909_v1/history_development/manifest.json \
  --phase development --periods 15 --out-dir "$SPIKE_OUTPUT_ROOT/development15"
.venv/bin/python -m yoyo.evaluation.altcoin_trend_research \
  --history data/altcoin_trends_20260909_v1/history_full_verified/manifest.json \
  --phase audit --periods 15 --selection "$SPIKE_OUTPUT_ROOT/development15" \
  --exposure-round "$SPIKE_EXPOSURE_ROUND" --out-dir "$SPIKE_OUTPUT_ROOT/audit15"
.venv/bin/python -m yoyo.evaluation.altcoin_trend_figures \
  --events "$SPIKE_OUTPUT_ROOT/audit/events_all.csv.gz" \
  --history data/altcoin_trends_20260909_v1/history_full_verified/manifest.json \
  --out-dir "$SPIKE_OUTPUT_ROOT/gallery" --phase recent_test \
  --selection "$SPIKE_OUTPUT_ROOT/development/selection_lock.json"
.venv/bin/python -m yoyo.evaluation.altcoin_cost_diagnostics \
  --results "$SPIKE_OUTPUT_ROOT/audit" \
  --history data/altcoin_trends_20260909_v1/history_full_verified/manifest.json \
  --derivatives data/altcoin_trends_20260909_v1/derivatives4h \
  --out-dir "$SPIKE_OUTPUT_ROOT/cost_diagnostics"
# The source report generator deliberately targets this experiment's artifact
# paths; call it for the official completed run, not to overwrite an older report.
