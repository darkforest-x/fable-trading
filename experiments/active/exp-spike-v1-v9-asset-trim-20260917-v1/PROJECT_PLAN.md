# exp-spike-v1-v9-asset-trim-20260917-v1

## Question

Owner: "去掉盈利最高和盈利最低的几个币种" — after the best and worst few assets
are removed, what is left of SPIKE V1 and V9?

## What moves

Nothing in the strategies. The only variable is which assets are included in the
aggregate. Rankings, exits, costs (0.2% nominal round trip), the entry rules and
the evaluation window are the published frozen ones.

## Inputs (already published, read-only)

- `exp-spike-v1-v8-be05-20260914-v1/delivery_v1/all_outcomes.csv.gz` — V1 native and
  V1 common-exit per-trade ledger, `rule=baseline, mode=fixed`.
- `exp-spike-v9-full-backtest-20260915-v1/statistics/full_v1/{trades,controls,summary}.csv*` —
  V9 per-trade ledger and its frozen matched random-entry controls.

## Method

1. Rank assets by realised closed net R inside each system.
2. Drop the top-k and bottom-k for k in {0,1,3,5,10}; recompute closed count,
   net R, R per trade, PF, and for V9 the frozen matched-control excess and
   month-block sign-flip p on the surviving trades.
3. Null control: 2000 random 2-asset drops matched on combined closed-trade count
   (+-25%), because removing the best asset lowers the total by construction —
   the question is whether it lowers it more than any comparable pair would.

## Refusals built in

- The untrimmed V9 row must reproduce the published `summary.csv` values
  (net R, matched pairs, paired excess, p) or the run raises.
- No price replay, no configuration change, no new holdout scoring.

## Honest bounds

The ranking uses realised outcomes, so every trimmed row is a post-hoc
decomposition, not a tradeable universe. V1 has no same-contract random-entry
control in the published artifacts, so its rows are concentration diagnostics
only and are not used to rank V1 against V9.
