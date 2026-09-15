# ETH BB × Stoch strategy v2 — owner revision 2026-09-16

## Authorized changes

The owner explicitly requests nine changes together: whole candle including wicks outside BB; 3% default stop; simpler/moved labels; native strategy; hidden BB with toggle; bottom-right panel; runner box ends at reversal and unweighted price R; plain text R/BE/TP labels; intrabar wick TP. This replaces the prior body-only confirmation. No V1 gate is requested yet.

## Rules and source

- ETHUSDT.P standard 5m only. V9 SMA200 ±2 population standard deviations. Original Stoch5/3/3 green crossover below20 and red crossunder above80; no WVF gate.
- Long high strictly below lower band plus green arrow on confirmed close. Short low strictly above upper band plus red. Equality fails. Full opposite composite exits the runner, even before first TP.
- Entry signal confirmed close; native entry fills next available tick (normally next open). Stop is3% from actual strategy average entry. No pyramiding. Full opposite composite closes immediately on the confirmed close, then opposite entry fills next tick.
- First upper-band touch long / lower-band touch short exits50%. The runner moves to entry after actual TP fill. Initial SL on the first half does not count as TP.
- Two strategy.exit brackets reserve exact half quantities with separate OCA groups. Fill executions update protective orders but cannot make new signal decisions using historical final OHLC.
- Developing BB touch is algebraically solved from preceding N−1 closes. At each normal confirmed close the script updates the next bar's resting limit; long rounds up/short down to the instrument tick. No same-bar final close is used to retrospectively fill at a wick.
- strategy properties: fixed1unit, initial100000, commission0.1% each side (the existing20bp round-trip convention), zero added slippage,100% margin. These are simulation settings, not a live position recommendation.
- R display = signed price distance / original3% risk. No weighting. Tester P/L still uses actual quantities and commission.

## Visual contract

Transparent red initial-risk box and green reward box. Once half exited, green right edge follows the runner until final exit and its height ends at runner price (zero-height on BE); TP label remains at the first fill. No highest-ever-profit or weighted profit claimed. TP left of its event, BE right and below/above entry, REV/R right of final exit. label.style_none throughout, no label background or arrow. Native broker markers belong to TradingView style settings.15groups default,30max; BB hidden toggle; bottom-right panel.

## Scope and validation

This is source conversion, not a return experiment. No market samples selected, no new holdout scoring, no model/live action. Native probe only changes selfTest default to true, suppressing market signal input and all orders. Fixtures use fixed2400/2530 price anchors and relative times for visual inspection. Independent algebra checks compare direct complete-window population-BB calculations at both sides of each trigger. Native order-fill sequences remain subject to the TradingView broker emulator; no full market ledger parity claimed.

## Files

Canonical source: yoyo/evaluation/pine/eth_bb_stoch_strategy_v2.pine.
Report: analysis/p1_eth_bb_stoch_strategy_20260916.md, immediately converted to HTML.
Previous version: exp-eth-bb-stoch-indicator-20260915-v1; preserved as historical source.
