# TradingView parity gate: V9 and V12F

The exact frozen V9 source hash now passes TradingView's official Pine v6
compiler on `OKX:ETHUSDT.P` 15m with zero compile errors.  That is a compiler
smoke only: the Basic plan's loaded chart range began after `researchEnd`, so
no historical trade export or broker-emulator ledger parity was obtained.

The local OKX research proxy is not venue parity.  Before any paper-forward
collection, open a pre-holdout chart first on the exact ETH perpetual venue
selected by the owner, set the chart to 15 minutes, set the strategy inputs to
2025-01-01 through 2026-02-28, preserve every other frozen default, and export
the Strategy Tester trade list.

Use one frozen Pine at a time:

- V9 baseline: `../pine/allin_eth_15m_v9_research.pine` (110 trades).
- Current research comparator V12F:
  `../pine/allin_eth_15m_v12f_ma6_w8_full_gate_paper.pine` (97 trades).

Normalize the export to `trades_normalized.csv` with these columns:

```text
direction,entry_time,exit_time,entry_price,exit_price,commission_total,net_profit
```

- `direction`: `long` or `short`.
- timestamps: ISO-8601 UTC values, for example `2025-01-01T12:15:00Z`.
- prices and P&L: plain decimal numbers; no currency symbols or thousands
  separators.
- `commission_total`: entry plus exit commission in account currency.
- one row per closed trade, ordered or unordered.

Run:

```bash
PYTHONPATH=. .venv/bin/python scripts/reconcile_pine_eth_15m_tradingview.py \
  --variant v9 \
  --input experiments/active/exp-pine-eth-15m-v1/tradingview/trades_normalized.csv
```

For V12F, keep its export in a separate file and run:

```bash
PYTHONPATH=. .venv/bin/python scripts/reconcile_pine_eth_15m_tradingview.py \
  --variant v12f \
  --input experiments/active/exp-pine-eth-15m-v1/tradingview/trades_normalized_v12f.csv
```

The tool fails closed unless all 110 canonical V9 trades, or all 97 canonical
V12F trades, match entry time, direction, exit time, and prices within one
research tick.  Fee and P&L columns are retained for manual venue accounting;
passing the OHLC ledger does not waive funding, slippage, or eligibility gates.

Do not put post-2026-05-04 exports here without owner approval.  The reconciler
rejects any row at or after repository holdout.
