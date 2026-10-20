# Frozen ETH exit search

Owner, 2026-10-20: asks for highest-return TP/SL parameters using the framework,
then explicitly chooses fixed risk per trade and total net R as primary.
This authorizes joint stop/exit parameter research, not a Pine/default promotion.

- Reuse the current 15m-watch/5m-entry 3,838 ETH signals and 3,838 matched controls.
  No body filter, admission cap, signal changes, data fetch, cost changes or funding model.
- Search 20 causal structural stops: last 3/5/8/12 completed 5m extrema plus
  0.025/0.05/0.1/0.2/0.4 percent entry-price buffer.
- For each stop: 160 trailing policies (1/3/5/10R activation, 1/2/3/4/6 ATR,
  no BE or 1/3/5R fee-aware BE, armed H1 reversal on/off); 16 fixed intrabar
  targets (1/2/3/5/10/20R, no BE or smaller 1/3/5R fee-aware trigger).
  Total 3,520 combinations. Management and BE use completed 15m closes and
  ATR14. Fixed targets replace trailing/H1 exits, not combine with them.
- Extend the existing C++ screening kernel and Nautilus native adapter.
  Resting limits fill at limit price in installed1.231.0; OHLC path O-H-L-C.
  Native reduce-only position closure owns sibling cancellation.
  A nonpositive short target is unreachable and never clipped.
- Every fixed-target candidate additionally receives a conservative stop-first
  dual-touch replay. Rank by that net R, using native-order net R for trailing.
  Show native and conservative results distinctly; no claim that bar OHLC
  determines the true intrabar sequence.
- Select on 2024 only, inspect2025/2026; rolling selection through2025 inspects2026.
  Full-history best and best per exit family are descriptive. Inspect one-axis
  neighbors, equal-notional returns, realized-cash drawdown and original20R tails.
  Prior history is known; no untouched/forward validation claim.
- Reuse cached baseline; baseline C++ parity across all7,676 rows. Native replay
  selected configurations, with original-risk/cost/protection/exit parity.
  Same-symbol/week/side/segment/causal-volatility controls get identical policies.
  Weekly inference and search multiplicity disclosed. AUC/top-decile inapplicable
  without a model score. Independent R is not a funded-account simulation.
- Retain summary/config/raw selected ledgers and receipt; no report requested.
  Freeze code and plan in main before screening.
