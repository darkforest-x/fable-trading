# V1/V8 frozen asset ranking

## Scope

Normalize existing frozen trades only. The study has three predeclared views:
`v1_common_execution_long`, `v8_long`, and `v8_both`. V1 is long-only; V8
shorts appear only as the additional component of `v8_both`. Native/original
V1 is a separate historical reference and is deliberately not mixed into these
rankings.

The full denominator is each view's closed trades. Development and validation
denominators additionally require the frozen confirmation-clock period and an
exit before that period's boundary. A development signal whose trade closes on
or after the split is absent from split scoring but remains in the full-closed
table. The confirmation clock is `signal_bar_open + timeframe_min`.

## Fixed outputs

The builder emits a normalized ledger, an asset ranking, a main asset ranking
with `n_closed >= 30`, a separate low-sample table, venue×asset and
asset×timeframe×side diagnostics, coverage, and a file-hash audit. Each table
reports trade counts, net-R and nominal-net-return PF separately, R summary
statistics, realized >=10R, and top-one trade concentration. Aggregate R is a
sum of trade outcomes, not a compounded account return; an asset can appear in
multiple venue/timeframe streams and therefore has repeated exposure risk.

Entry risk fraction uses frozen `initial_risk_frac`. The unchanged 0.2%
round-trip cost is also expressed descriptively as `cost_r=0.002/risk_fraction`
when finite. A mathematically available `net_return/net_r` fallback is labeled
explicitly and never used when net R is zero.

## Reproducibility and limits

The builder authenticates both top-level manifests, V8 same-entry evidence, and
hashes every consumed trade file. It does not use the mutable historical
`replay_ledger_link_receipt.csv.gz`. This is historical, nonblind evidence with
holdout-era use #1 authorized by the owner. It does not establish a tradable
asset exclusion policy or a causal V1/V8 performance difference.
