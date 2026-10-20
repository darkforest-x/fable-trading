# Reusable native framework adapter

Owner authorization: 2026-10-20 request to integrate the previously suggested
open-source backtest frameworks and reduce repeated code/context work.

Only variable: execution engine. Reuse the frozen PEPE candidates, controls,
completed candles, initial stops, corrected 5R threshold, cost and split ends.
Run baseline and the already evaluated BE3 policy, without a new optimization.

Use installed NautilusTrader 1.231.0 with independent HEDGING position IDs.
The framework owns market fills, STOP_MARKET matching, modifications, fees and
position lifecycle. Strategy code owns causal close-time management decisions.
Do not replay precomputed exits through the engine or call the bespoke C++ loop.

Acceptance: native behavioral tests; exact entry/exit/management-field parity
against both frozen 3,775-trade ledgers; native order/position/fee reconciliation;
reuse cached inputs and completed results on an unchanged command. Store raw
fills/trades and a compact receipt. No new standalone report or UI.

Reproduce: `scripts/backtest-range`; run again to reuse the completed receipt.
For a new authorized study copy config, edit stop/management parameters and use
a new `--output` directory. Remove `golden_ledger` for changed policies; golden
validation must fail if a changed policy is compared to an unchanged reference.
`breakeven_r: null` disables BE. No fixed intrabar TP adapter yet, because native
O-H-L-C collision ordering differs from the earlier stop-first experiment.

No data download, dependency installation, training, production or account
actions. Native liquidity is synthetic and the account is frozen; outputs are
independent R and constant-notional study returns, not portfolio performance.
