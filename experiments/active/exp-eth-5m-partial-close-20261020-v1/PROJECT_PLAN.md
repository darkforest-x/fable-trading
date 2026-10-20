# Frozen ETH partial-close comparison

Owner: “试试”, 2026-10-20. Scope: current15m-watch/5m-entry ETH candidates, no candle-body filter, no Pine change.

- Reuse3838signals and3838same-symbol/week/side/segment/causal-volatility matched controls;2024-08-02 through2026-09-22 14UTC. No new data or candidate construction.
- Baseline:5completed5m extrema plus0.05percent buffer;15m close5R starts ATR14x4;armed H1 reverse close exit;20bp original-notional cost, no breakeven.
- Six predeclared alternatives: first surviving5m close at least1R,3R,5R;realize25percent or50percent once. Remaining shares follow identical stops/management. No simultaneous additional breakeven or size-dependent entry lock. Stop before close reduction; terminal close exit before redundant reduction. Close fills can exceed threshold and do not count wick-only touches.
- Reuse existing Nautilus1.231.0 native adapter, adding actual partial market reductions and remaining-share protective stops. Whole research quantities divisible by fraction denominator avoid rounding dust. Initial risk remains original full position. Allocate fee by fill quantity; never charge20bp anew per reduction.
- Primary selection:2024 mean net notional bp including baseline; frozen candidate then2025/2026. Also show independent netR,netwin,realized-cash drawdown and original10R/20Rtail profit retention. RealizedR drawdown is not portfolio/MTM drawdown. All historical dates previously seen.
- Controls receive identical exits. Weekly paired randomization,20,000 sign flips and5,000 bootstrap samples;Holm six exit contrasts. No classifier score exists, so AUC/top-decile are inapplicable.
- Native tests plus bounded no-partial regression, independent first-close/weighted-return oracle and per-fill accounting on all new rows. Baseline not fully replayed. Raw ledgers retained, summaries/receipts registered; no report requested.
