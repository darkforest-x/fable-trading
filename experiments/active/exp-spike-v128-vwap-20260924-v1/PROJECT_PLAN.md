# SPIKE V12.8 session VWAP / TWAP admission experiment

Owner authorized research and backtesting on 2026-09-24. This is an experiment
specification and reproducibility record, not a separate delivery report.

## Frozen question and design

Test whether being closer to the UTC-session volume-weighted average improves
SPIKE entry quality and retains large winners. Compare the original baseline,
VWAP-distance admission and same-window TWAP-distance admission separately.
VWAP uses cumulative chart HLC3 times base volume / cumulative base volume;
TWAP uses cumulative HLC3 / complete chart-bar count. Session follows bar open
at UTC midnight. Partial first sessions, gaps and invalid inputs remain unknown.
The denominator is exactly the parent's SMA-seeded SMMA14 true range.

Use every authenticated baseline candidate and fixed-entry outcome in the
existing 638-contract Binance 15m/60m long-history archive. Do not select today's
winners or fetch another venue. All original exits, opposite raw events, risk,
next-open fills and round-trip 20bp cost stay fixed. Recompute serial occupancy
from all candidates, not a filtered closed-trade table. Assert original serial
entries, outcomes and controls are reproduced. Cache reuse is permitted because
eligibility changes cannot alter a fixed entry's original exit calculation.

Use raw candidate absolute distances from 2023-01-01 through 2024-12-31 to fit
one median per timeframe and feature, with no outcome/censor selection. Gates
admit distance <= median. The earlier q10 is a fixed top-decile diagnostic.
Joint entries use the raw-signal cutoff, without a separate search. Earlier
performance is descriptive, using its fitted cutoff. At 2025-01-01 all later
policies inherit the original baseline's open position, then run independently;
this avoids a fitted early gate changing the validation starting position.
Cross-cut outcomes cannot enter early completed-trade metrics.

## Evidence and interpretation

Report early/later periods, timeframe, direction and ordinary/joint separately.
Include filled/closed/censored/cross-cut counts, frequency, net win rate, gross
and net bp, net R, PF, >=3/5/10R counts and original winner retention/new winners.
R uses original entry risk. No summing overlapping arms into a portfolio.

Use the inherited deterministic one-draw same-symbol/direction/week/fold/
causal-volatility random entry. Main comparator additionally requires its
feature to pass the same gate; rejection, unknown features and censoring are
not redrawn. Also retain unconditional random comparison and gate support.
TWAP is the single-price-feature control. Weekly clustered uncertainty respects
cross-symbol calendar shocks. Four later raw/timeframe/feature excess tests form
one Holm family; p<0.01 is required for a statistical advantage claim, together
with positive after-cost means and tail/frequency evidence. Joint results are
diagnostic. Missing support or conflicting performance yields inconclusive or
rejected, not a new default. AUC is descriptive, never the success criterion.

No new training, Pine delivery, production admission or trading is authorized
by this research. Event-anchor VWAP and TWAP order execution are not silently
added as extra optimizations. The current dataset is a frozen retrospective
universe; listing coverage, tick changes, native parity and real execution
limits remain explicit.

## Reproduction

Commit builder, tests, config and this plan before consuming market outcomes.
Run unit tests, then a BTC/ETH integration smoke, then the complete universe.
Keep failed run directories. New outputs never overwrite prior results.

```bash
.venv/bin/python -m pytest -q tests/evaluation/test_spike_v128_vwap_study.py tests/evaluation/test_spike_v128_vwap_stats.py
.venv/bin/python -m yoyo.evaluation.spike_v128_vwap_study --output experiments/active/exp-spike-v128-vwap-20260924-v1/smoke_v1 --symbols BTCUSDT ETHUSDT --workers 2
.venv/bin/python -m yoyo.evaluation.spike_v128_vwap_study --output experiments/active/exp-spike-v128-vwap-20260924-v1/run_v1 --workers 4
.venv/bin/python -m yoyo.evaluation.spike_v128_vwap_stats --run experiments/active/exp-spike-v128-vwap-20260924-v1/run_v1 --output experiments/active/exp-spike-v128-vwap-20260924-v1/summary_v1
```

Official definitions consulted 2026-09-24:
- https://www.tradingview.com/support/solutions/43000502018-volume-weighted-average-price-vwap/
- https://www.tradingview.com/blog/en/anchored-vwap-is-on-tradingview-18573/
- https://www.okx.com/zh-hans/help/xiii-time-weighted-average-price-twap

The primary source describes formulas/tools, not profitability evidence.
