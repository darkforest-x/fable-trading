# ETH BB × Stoch v2 — frozen first economic replay

## Authorization and fixed scope

Owner asked “回测一下” after the nine-change v2 strategy delivery. Replay that exact source and defaults, without parameter search. This is a historical offline order-rule simulation on the existing OKX ETH perpetual native 5m prefix. It is not a native TradingView broker-ledger parity claim. No live trading, V1 gate, training, promotion or new venue. Only main, no branch/worktree. Source/config/builders must be committed before any price/outcome read.

## Data and causal boundary

Use the existing timestamp-first CSV in config. Read the full available prefix with yoyo.data.spike_fanshen_prefix.read_prefix; stop before any bar closing after 2026-05-01T00:00Z. This deliberately precedes the central May4 holdout. Validate complete epoch-aligned positive OHLCV, no duplicate/gap or filling, and hash admitted bytes only. Report actual coverage, rows, warmup and excluded-price count. BB requires 200 closed bars. Stoch is the original 5/3/3 arrow, no WVF filter. Volatility matching uses current and prior completed bars only.

## Frozen execution

- Whole candle strict high<lower for long, low>upper for short and same-bar Stoch arrow. Signals at closed bars, next-open entry; initial stop 3% from actual entry.
- Hold one position. First opposite BB touch closes half intrabar. Dynamic touch is solved from the previous199 closes, long rounded up and short down to .01. Stop moves to entry only after this fill. Continue the remaining price path without reusing an earlier wick.
- If a newly activated BE is already marketable at the TP event, approximate the remaining exit at that observed event price and flag it. Distinguish same-open activation from an intrabar target that has moved to the losing side. The Pine has no profit-only filter; do not add one. Exact next-tick native broker parity is unavailable from OHLC.
- Full opposite composite closes the rest at that bar close. The opposite entry is eligible at the next open. Same-bar post-stop closed signals can enter next open. No pyramiding.
- Primary within-bar path: TradingView documented open-nearer-high then O-H-L-C, otherwise O-L-H-C; exact distance ties choose low first and are disclosed. Standing stops/limits crossed by an opening gap fill at open. Newly entered positions already beyond the first target use an open approximation and are flagged.
- Fixed sensitivity: adverse excursion first (O-L-H-C long / O-H-L-C short). This is a path sensitivity, not a mathematical lower bound. Do not choose a winning path or retune parameters.
- Price gaps cannot skip source intervals. Boundary positions are separately marked at last close with only actually incurred entry/partial fees; no invented liquidation fill or fee. Natural-trade totals do not silently close them. The boundary-marked comparison therefore is not a fully liquidated return.
- Normalize to 1 ETH per full position. Fee=.1% per actual executed notional, zero extra slippage, no funding. This is normalized cash accounting, not a claim about TradingView/exchange contract point value. Initial-risk R=3% entry price. Economic R correctly weights fills; chart tail price R remains unweighted as requested.

## Controls and analysis

For every accepted serial entry, freeze five random signal-bar draws before reading their outcomes: same ETH instrument, direction, UTC signal-close month and causal volatility tercile. Volatility is SMA14 true range divided by close; tercile thresholds come from the preceding120 ratios. Require complete BB warmup and next available bar. Exclude the actual signal itself; draw without replacement within its group. Keep censored draws, never redraw based on outcomes. Controls use identical execution rules and cost. Reuse draws across path modes for shared entry indices. Controls may overlap and are event diagnostics, not a serial investable control portfolio.

Report natural and censored counts, gross/net R, win rate, profit factor, average gain/loss, realized drawdown, fixed-1ETH cash net, consecutive losses, partial/BE/reversal/SL breakdown. Pair only naturally closed actual events with all five naturally closed controls for the primary null comparison and disclose unpaired counts; include an all-draw boundary-marked comparison to expose selection effects. Month-block one-sided sign flips on paired net-R differences: exact enumeration for <=16 months, otherwise9999 seeded signs. It tests a symmetry null, not proof of future profitability. Diagnostic partitions: full interval, first/second chronological halves split at permitted interval midpoint (with continuous positions), long/short and UTC signal-close months. Every direction-performance table includes the matched-control mean and excess or says no eligible pair.

No ranking model exists, so validation AUC, top-decile sorting and feature ranking are not applicable. The matched-entry null substitutes for that absent model, not fabricated values. Prior v1 was an indicator-only implementation without a frozen economic result; comparison with v1 is N/A, not a new backtest or extra parameter trial.

## Delivery and validation

Synthetic tests for wick equality, causal BB targets, partial+BE order, earlier wick, gaps/SL, full reversal, fees and boundary handling. Main locally verifies delegate tests and audits accounting. Focused causality/boundary checks; unrelated repository failures retained and disclosed. Save source receipt, committed code receipt, every signal/trade/fill/control draw, JSON metrics, reproducibility commands and honest limitations. Write analysis/p1_eth_bb_stoch_backtest_20260916.md then immediately render HTML and open it. Register artifacts and capture the outcome in the owner's Spike Notion knowledge base as research, not validated/live. Record a learning note for nontrivial replay methodology.
