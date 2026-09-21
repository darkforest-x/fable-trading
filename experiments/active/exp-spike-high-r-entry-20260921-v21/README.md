# SPIKE High-R Entry V2.1

Research only: original V9 long reference risk must be at or below the preceding three complete calendar months' tenth percentile for the same timeframe. Frozen original exits/costs and raw reverse signals remain unchanged. No future labels are used to compute thresholds; the hypothesis itself was selected after exposed V2 diagnostics.

```bash
.venv/bin/python -m pytest tests/evaluation/test_spike_high_r_entry_v21.py tests/evaluation/test_spike_high_r_risk_study.py -q
# Commit builders before calibration; commit calibration thresholds and receipt before replay.
.venv/bin/python -m yoyo.evaluation.spike_high_r_risk_calibration --output experiments/active/exp-spike-high-r-entry-20260921-v21/calibration_v1
.venv/bin/python -m yoyo.evaluation.spike_high_r_risk_study --output experiments/active/exp-spike-high-r-entry-20260921-v21/run_v1 --workers 3
.venv/bin/python -m yoyo.evaluation.spike_high_r_risk_report --run experiments/active/exp-spike-high-r-entry-20260921-v21/run_v1 --output experiments/active/exp-spike-high-r-entry-20260921-v21/statistics_v1
```

Production/training eligibility remains false. See PROJECT_PLAN.md for the chronological acceptance contract.
