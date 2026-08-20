# TradingView parity gate

The local OKX research proxy is not venue parity.  Before any paper-forward
collection, compile `../pine/allin_eth_15m_v9_research.pine` on the exact ETH
perpetual venue selected by the owner, set the chart to 15 minutes, preserve
the frozen defaults, and export the Strategy Tester trade list covering
2025-01-01 through 2026-02-28.

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
  --input experiments/active/exp-pine-eth-15m-v1/tradingview/trades_normalized.csv
```

The tool fails closed unless all 110 canonical V9 trades match entry time,
direction, exit time, and prices within one research tick.  Fee and P&L columns
are retained for manual venue accounting; passing the OHLC ledger does not
waive funding, slippage, or eligibility gates.

Do not put post-2026-05-04 exports here without owner approval.  The reconciler
rejects any row at or after repository holdout.
