# COMP 1H moving-average sequence case

Owner-selected known-outcome case, not a new strategy or holdout validation.

- Report: `analysis/p0_comp_ma_sequence_case_20260913.md`
- Delivery: `analysis/html/p0_comp_ma_sequence_case_20260913.html`
- Source: existing read-only OKX COMP 15m cache, complete-hour aggregation.
- Three entries and 313 context bars; original ordering, evolving slopes, and future ordering are separate clocks.
- Authorized holdout-era view #1; no tuning, training, promotion or online changes.
- Builders: `yoyo/evaluation/comp_ma_sequence_case.py`, `comp_ma_sequence_chart.py`.
- The local CSV/PNG/HTML artifacts are hash-bound by `delivery_manifest.json`; no image pack added to Git.
- Notion: https://app.notion.com/p/3da8856479af818bb14fdf5425a05205
