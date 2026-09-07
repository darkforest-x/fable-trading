# ALLIN V7 ETHUSDT perpetual 4h replay

Owner request: “你直接帮我回测试试看”, 2026-09-07.

## Frozen scope, before outcomes

- Source: exact attachment 6835d2f3-662b-49bd-ba38-7c6f32073664/pasted-text.txt.
- Venue assumption: Binance USD-M ETHUSDT, pending optional owner clarification.
- Local public Binance archive: 15m OHLCV from 2020-01-01 through 2026-04-30; verify the existing audit SHA256, cadence, and all 16 constituents per UTC 4h bar. No fetching, writing source candles, or holdout access.
- Warm-up: 2020; trade evaluation: 2021-01-01 inclusive to 2026-05-01 exclusive. Continuous account and calendar-year attribution, plus a separately reset 2025-2026-04 diagnostic. Neither period is a pristine validation set.
- Literal source signal parameters; UTC calendar filter, HK sizing boosts; preserve stale cooldown boolean, same-direction stop resets, shared reversal stop variable, no initial stop order until the entry bar closes.
- Historical close calculations and next-open market fills. Existing stop orders may execute intrabar. Stop/market priority on reversals consolidated as a single reverse, explicitly not TradingView compiler parity. Preserve current-position stop mutations before the new position exists. No intrabar signal simulation, Bar Magnifier, exchange liquidation, funding, or separate slippage model.

## Separate single-variable diagnostics

1. Original leverage, delayed stop, zero cost: reproduce source's unspecified-cost default.
2. Same policy, 0.1% commission on each fill notional (nominal 0.2% round trip).
3. Relative to (2), only activate the signal-price initial hard stop on the entry bar.
4. Relative to (2), only replace the entire 4x/8x sizing schedule with constant 1x.
5. Relative to (2), only replace the entry condition by the unfiltered SMA10/60 cross; same calendar, volatility, cooldown and exits.

No optimization, grid search, trained model, barrier change, or production action.

## Controls and statistical limits

For each closed trade, select up to three non-signal entries without replacement per case, same UTC month, HK six-hour block, causal ATR-percent quintile (trailing 252 bars, prior-bar cutoff), direction and entry-stop policy. Controls share the source exit machinery and commission but carry their own entry/ATR; use an administrative end boundary only. No copied realized holding horizon and no outcome-dependent matching. Use a fixed plus/minus 12-bar exclusion around the case signal, not its realized holding interval; retain missing matches and censoring counts. Controls may overlap each other and other cases, so use month-cluster sign-flip tests of paired excess, with 10,000 permutations. Report coverage, matched-case and control means together. Control accounts are independent event comparators, not a jointly tradable portfolio.

Report total and yearly equity, bar-close and ordered-OHLC path drawdown, closed-trade win rate, currency and unit-return PF, long/short statistics, best-trade contribution, fees, raw candidates, actual entries, and open-end mark-to-market separately. abs(osc) AUC/top-decile/permutation are diagnostics only: original strategy does not trade a top decile. Include SMA-only comparator and the matched random controls in economic tables. No result is sufficient for production acceptance.

## Evidence

Commit implementation before market replay. Test adverse intrabar movement with delayed versus immediate stops; no same-bar BE retroactivity; strict > trigger; stale cooldown; same-side stop reset; reversal global stop; per-fill fees; prefix causality and full 4h aggregation. Store source/config/code/data hashes, ledgers, controls, equity, validation and report. Markdown report must immediately be converted with scripts/md_to_html.py and opened as HTML. Historical files are not rewritten.
