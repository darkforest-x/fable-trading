# 2m / 3m data-support evidence

- owner request: try 3m and 2m levels (2026-07-12)
- OKX live API: both `bar=2m` and `bar=3m` returned code `0` and five rows
- data support: `BAR_CHOICES`, timedelta mapping and updater filename parser include 2m/3m
- red test: 3 failures before implementation (`normalize_bar` and two filename cases)
- focused verification: 36 passed; full suite: 220 passed
- 2m smoke history: BTC/ETH, 86,522 rows each after updater QA, 120 days, no duplicate/gap/bad OHLC
- 3m smoke history: BTC/ETH, 57,705 rows each after updater QA, 120 days, no duplicate/gap/bad OHLC
- holdout evaluated: false
- ACTIVE / 15m ledgers changed by this task: false
- report: `analysis/p2b_hf_2m_3m_data_feasibility.md`
