# SPIKE High-R Entry V2

Offline single-variable entry study: original V9 long admission plus a strict prior-20-bar high breakout at the completed signal close. Original exits and shorts remain unchanged. Target higher win rate and netR-over10 precision; future outcomes never gate admission.

See PROJECT_PLAN.md and config.json for the frozen contract. Run only after committing builder and tests:

```bash
.venv/bin/python -m pytest tests/evaluation/test_spike_high_r_entry_v2.py tests/evaluation/test_spike_high_r_entry_study.py -q
.venv/bin/python -m yoyo.evaluation.spike_high_r_entry_study --output experiments/active/exp-spike-high-r-entry-20260921-v2/run_v1 --workers 3
.venv/bin/python -m yoyo.evaluation.spike_high_r_entry_report --run experiments/active/exp-spike-high-r-entry-20260921-v2/run_v1 --output experiments/active/exp-spike-high-r-entry-20260921-v2/statistics_v1
```

Not trained, not promoted, not a live 10R guarantee. Reused historical data is not blind validation.

Result (2026-09-21): Rejected: full win rate rises to30.21% but netR>10 precision falls to0.8186%;later economics remain negative. Full3531 streams completed withzero failures. See analysis/p1_spike_high_r_entry_v2_20260921.md.
