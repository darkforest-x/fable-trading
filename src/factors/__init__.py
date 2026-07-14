"""Alpha-factor library for the judgment layer (H19).

A curated, LOOKAHEAD-FREE, crypto-15m-appropriate subset of classic factor
families (Alpha101/Kakushadze, GTJA191, Qlib Alpha158). NOT imported from any
external framework -- these are public formulas re-expressed as causal pandas.

Rules enforced here:
- every factor uses only bars <= i (no shift(-n), no future windows);
- no cross-sectional rank (we score per-symbol time series, not a panel);
- no fundamentals/calendar (24h crypto has none).
Each factor's docstring names the exact columns and windows it touches.
"""
