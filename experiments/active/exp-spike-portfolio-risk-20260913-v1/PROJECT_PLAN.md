# SPIKE portfolio-risk gate study — fixed read-only comparison

This nonblind reused-history study consumes the completed all-source V7/V8
cross-venue `event_members.csv.gz` ledger and the exact frozen serial-replay
trade receipts. It does not change signal quality, entry, exit, costs,
thresholds, execution, monitoring, models, or any live surface.

The upstream outcomes extend beyond the project holdout boundary of 2026-05-04.
Owner's instruction to execute all five studies, together with the prior
explicit all-dates authorization, records **holdout consumption #1 for this
exact portfolio-risk configuration**. `holdout_usage.json` preserves the
nonblind receipt; it grants no production status.

The structural comparison is fixed before this run:

1. every source confirmation;
2. one causal event leader per upstream folded event;
3. event leaders plus one-open-position-per-underlying-asset; and
4. event leaders plus a five-open-position concurrency cap.

The last two are separate single-variable risk policies. At a frozen entry
clock they inspect only existing accepted positions' entry/exit clocks and
identity. Exits release capacity before same-clock entries; same-clock entries
use an outcome-free receipt-ID order. Frozen `net_r` is reported after
selection. It is the existing return divided by that trade's frozen initial
price risk; the inherited trade outcome already includes the replay's fixed
0.2% round-trip cost. No trade is re-entered, re-exited, repriced, or
resized.

The authorized frozen artifacts contain no multi-asset OHLC return panel, so
the proposed past-30-day correlation gate is omitted. Realized future trade
outcomes are never substituted for causal correlation estimates.

Outputs retain a policy-by-candidate audit ledger, reason reconciliation,
separate development/validation metrics, and an explicit non-production
manifest. Cumulative R and profit factor use closed frozen net R; censored
trades reserve capacity until their frozen boundary but are excluded from those
realized-outcome metrics. Drawdown is closed-trade, exit-time drawdown in R and
is not intrabar portfolio mark-to-market drawdown.
