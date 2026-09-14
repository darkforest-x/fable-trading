# ETH release strategy: 15m / 1h / 4h preregistered research

Owner request on 2026-09-14: independently run a detailed backtest, identify strengths/failures, optimize one version for ETHUSDT.P 15m, 1h and 4h, and enable goal mode. Research implementation is authorized. No live order, alert, deployment, threshold preset or holdout authorization is inferred.

## Frozen scope before new outcomes

- Primary instrument: OKX ETH-USDT-SWAP (TradingView OKX:ETHUSDT.P), standard OHLC. One local source of 15m candles, timestamp-first prefix parsing; no source substitution. Candidate source: data/kline_deep/okx_ETH_USDT_SWAP_15m_158499.csv. Final source identity and coverage must be recorded before replay.
- End exclusive: 2026-05-01T00:00:00Z, ahead of the central holdout boundary 2026-05-04. Never parse price/volume of a row at or beyond the permitted endpoint. No holdout fetch, scoring or optimization.
- Warmup: all available permitted rows before 2023, requiring at least 512 fully formed 4h bars before evaluation. If unavailable, stop and revise dates using coverage only, before observing returns.
- Development 2023-01-01 through 2024-12-31; chronological independent 2023 and 2024 development folds. Freeze a candidate separately per timeframe from development only. Validation: 2025. Later retrospective check: 2026-01-01 through 2026-04-30. This history has been used by prior research; never describe it as pristine unseen evidence. Do not change selected candidates after viewing validation.
- A final continuous 2023–Apr2026 account is descriptive, not a selection input. Each period otherwise starts flat with 500 USDT. Last-close settlement, if used, is explicitly administrative and charged exit fee; natural exits and end-censored positions remain separately identified. No trades or outcome labels cross a selection boundary.
- Execution baseline: original historical-close signal clock, next-open market fills, original stop initialization delayed until entry bar close. Preserve source quirks for the original branch; state clearly that simultaneous reverse/close orders are a consolidated Python model until native parity exists.
- Primary cost: 0.1% each fill notional (nominal 0.2% round trip), frozen. Original zero-cost TV-style reproduction is a separately labelled diagnostic, not a deployment cost estimate. No alteration to ATR14 x4, 3% hard-stop cap, BE1.5% trigger /0.1% offset, volatility bounds or oscillator0.2 threshold. Funding/extra slippage are not silently treated as known zero; report available coverage and limits. Research notional of 1x is not a change to any live position.

## Single-variable engineering sequence

Every row points to exactly one parent; intermediate failures are retained.

1. O0 original zero cost.
2. O1 versus O0: commission only, 0.1% each fill.
3. E1 versus O1: notional schedule only, fixed1x.
4. E2 versus E1: initial protection creation time only, active at entry.
5. E3 versus E2: same-side stop replacement only, monotonic tightening.
6. E4 versus E3: pending/active stop ownership only, isolate opposite entry stop and reset on actual entry.
7. E5 versus E4: cooldown boolean calculation time only, after incorporating latest closed P/L. This is the predetermined engineering baseline irrespective of interim profitability.

Add an O1→immediate-stop-only diagnostic to show high-leverage sensitivity without confusing sizing. The engine must preserve original4h synthetic ledger parity under original flags. Signals are confirmed close in all tested policies; realtime-tick performance is not claimed.

## Bounded development candidates

All are independent single changes from E5, with no sequential combination of the winning changes in this iteration:

- C0: E5 unchanged.
- C1: slow SMA length60→40 only.
- C2: slow SMA length60→80 only.
- C3: only add directional slow-SMA one-bar slope to flat entry eligibility; reverse exits stay unchanged.
- C4: disable profitable-trade cooldown only.
- C5: remove Sunday exclusion only; hourly exclusion and all other rules unchanged.

No TP/SL/BE parameter search. No new candidates after outcomes. Original signals and same-cost SMA-only entry are mandatory comparators.

Selection per timeframe is deterministic and development-only. Compare independent2023 and2024 folds. A challenger needs at least20 combined naturally closed trades, positive account return and matched mean excess in each fold, no nonpositive equity, maximum fold path drawdown no larger than C0, and worst-fold return strictly above C0. Among admissible candidates, rank by worst-fold return, then lower worst drawdown, then identifier. If none qualifies, retain C0 and explicitly state no signal optimization was established. These are exploratory selection rules, not the project's statistical acceptance or a promise of profit.

## Matched controls, inference and comparisons

- For each signal event, sample up to3 unique non-signal entries, same instrument/timeframe, side, UTC month, HK six-hour block and causal prior252-bar ATR-percent quintile. Enforce candidate flat-entry eligibility. Exclude ±48 hours around case signal; never use realized holding horizon, future volatility or future outcomes for matching. No reused control signal+side within a policy/period. Missing support/censoring is reported, not filled or silently dropped.
- Each control is an independent one-event account using the same stop, BE, reverse exit clock and cost. It is not a simultaneously tradable random portfolio. Report case/control means on exactly matched support, coverage and full-case mean separately. Period-end treatment must be identical for cases and controls.
- AUC and top-decile gross/net returns using abs(osc) are diagnostics only; the Pine trades all eligible signals. Report score permutation p (10,000, seed20260914), monthly-cluster sign-flip p and monthly-cluster bootstrap CI for matched excess. No IID trade t-test for overlapping paths. Retain all development comparisons and adjust the family of selected validation matched p-values with Holm; project p<0.01 standard remains unchanged.
- Primary economic outputs: settled and marked account return, path/close drawdown, gross/net unit expectancy, net win rate, currency/unit PF, fees and turnover, holding hours, long/short, annual/monthly stability, worst losses, top1/top5 concentration, 1R/3R/10R MFE and capture, exposure, skipped counts, first-entry-bar unprotected touches, source/gap coverage, and equal-cost simple-MA baseline. All directional result tables include matched controls or explicit support unavailability.
- If implemented, same-source15m intrabar fill precision for1h/4h is one isolated execution-precision diagnostic on frozen originals/selected candidates. Signals and BE updates still occur only at parent closes. It is not tick data and does not justify intrabar feature access.

## Quality and completion

Only main, no branch/worktree, no dependencies or production edits. Commit builder, plan and tests before market replay. Synthetic chronology/fee/causality/aggregation tests; immutable source-prefix hash and receipts; freeze selected configuration before validation. Independent bounded code and accounting review; address concrete defects with failed results retained. New historical computation caused by a bug fix must be separately labelled, not overwrite evidence.

Deliver full Markdown immediately rendered to HTML, original and optimized-by-timeframe ledgers, matched controls, plots, JSON selection/validation receipts, one Pine research source with frozen timeframe presets, artifact/experiment registry entries, learning note and Spike Notion evidence. Native Pine compilation/ledger parity and funding gaps must be explicit if unresolved. If a timeframe remains unprofitable or unsupported, deliver that negative conclusion instead of continuing until it looks good.
