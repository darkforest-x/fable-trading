# BTC RSI1h / six-MA5m: short-only entry sensitivity

Owner asked on 2026-09-20 whether long/short were separated and how shorts perform.
The previous bidirectional short subgroup had 189 closed trades and -54.6958649073 net R.
That subgroup is not a standalone short-only replay because long holdings block entries.

Single variable: disable long entries. Keep the exact prior frozen OHLCV, RSI/SAR,
six close MAs, post-hour-close five-minute confirmation, one-position occupancy,
original hourly high stop, 3R target, 0.002 round-trip cost, and start/end boundaries.
Long diamonds remain events and replace pending short setups; they cannot open
positions. Implement by setting only the prepared sixma_long admission array to
false in a copied research context. Do not change the shared simulation engine.

Use prior three-year window 2023-09-19T21:00Z to 2026-09-19T21:00Z, split at
2025-09-19T21:00Z, flat initial state. Data are already frozen by the parent study.
Compare prior short subgroup versus standalone short-only stream, including exact
shared/added/removed diamond membership and retained-trade fill parity. Keep one
terminal open trade separate if present. Same matched random timing null and
cost/reporting conventions as parent study; no tuning or blind-validation claim.

Commit builder before scoring. Preserve previous report and outputs. Source report:
analysis/p1_btc_rsi1h_sixma5m_20260920.md. No Pine, production or training changes.
production_eligible=false; training_eligible=false.
