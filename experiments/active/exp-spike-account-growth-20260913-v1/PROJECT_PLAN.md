# SPIKE V1 / V7 shared-account growth study

## Decision to make

Test whether the existing frozen V1 and V7 event ledgers can support a real shared 1000 USDT account under fixed-dollar or compounded position risk of 3%, 5%, or 10%, and whether any result remains usable across timeframes and market regimes.

The target of 100,000 USDT is an evaluation threshold. It is not assumed achievable and is not used to alter entries, exits, or choose a favourable event order.

## Frozen comparison

- Fair signal comparison: `v1_common_execution_long` versus `v7_bb_long` under identical common execution.
- Original V1 account reference: `v1_native_long`; its archived exit contract differs and is never presented as a fair common-execution comparison with V7.
- Operational V7 reference: `v7_bb_both`, reported separately because V1 has no short counterpart in this frozen ledger.
- Periods: development from 2024-09-10 through 2025-09-09 UTC; validation from 2025-09-10 through 2026-09-09 UTC; full period only for descriptive history.
- Timeframes: 30m, 1H, 4H, and a shared cross-timeframe account.
- Venue scopes: all covered Binance/OKX/Gate streams combined, plus OKX-only.
- Account paths: fixed risk and compounded risk, each at 3%, 5%, and 10% per accepted position.

## Account contract

1. Exits are processed before entries with the same timestamp.
2. Simultaneous entries are ordered by a stable outcome-free hash and frozen seed 0.
3. Only one open position per base asset is allowed across venues and timeframes.
4. Open initial risk may not exceed 10% of current realized balance.
5. Gross entry notional may not exceed 3x current realized balance.
6. Fixed sizing risks 3/5/10% of the initial 1000 USDT. Compound sizing risks the same fraction of current realized balance.
7. New entries stop permanently below 200 USDT; already-open positions still realize their frozen exits.
8. Gap losses may exceed 1R. Censored trades reserve capacity through their boundary, then realize zero and remain labelled censored.

These are fixed engineering assumptions for feasibility, not optimized strategy parameters. Funding, slippage, market impact, maintenance margin, liquidation, and intratrade mark-to-market equity are unavailable in the frozen source. Any path that needs those omitted effects to survive is unsupported.

## Required evidence

- Complete summary for every frozen arm × venue scope × timeframe × period × sizing × risk combination.
- Accepted trades and rejection reasons for full-period paths.
- Final balance, return multiple, closed-balance drawdown, floor/bankruptcy state, capacity rejections, realized ≥10R events, and concentration in the largest winner.
- Development and validation shown side by side. Full-period best configurations are hindsight descriptions only.
- Causal BTC/ETH 4H regime attribution; trailing-24h normalized launch breadth with a development-distribution P80 threshold; Beijing entry-hour/weekday/month diagnostics; and daily realized-PnL concentration.
- Existing matched-random evidence from the source V1/V7 experiment must be carried beside strategy conclusions; no new signal edge is inferred from account sizing alone.
- A direct answer on whether any executable path reaches 100,000 USDT. If none does, no “route” may be fabricated.

## Guardrails

- This experiment consumes the owner-authorized frozen holdout-era ledger for account configuration exposure #1.
- No parameter search over entries or exits, no current leaderboard symbol selection, and no post-hoc removal of losses.
- No ACTIVE, monitor, Bark, Telegram, TradingView, API key, position, order, or deployment change.
- Builder code and tests land before full artifacts are generated.

## Outcome — 2026-09-13

- Status: **rejected for live use**. All 8 development-selected configurations finished the independent validation account below 1,000 USDT; none reached 100,000 USDT.
- The highest full-period terminal balance was 10,510.49 USDT for the hindsight-only V7 long / OKX / 4H / fixed-10% path. Its 32 deterministic same-time-order replays were unstable: median terminal balance 967.98 USDT and only 2/32 finished above 1,000 USDT.
- One V7 bidirectional / OKX / all-timeframe / compound-5% seed touched 123,534.27 USDT, then finished at 7,083.56 USDT after a 94.27% realized drawdown. Only 2/32 full-period order seeds touched 100,000 USDT, versus 0/32 in independent validation.
- No matched event control passed the project p<0.01 threshold. The 11/192 development-and-validation-positive grid cells are post-hoc leads, all from V7 bidirectional, and are not a validated signal edge.
- A single research-only forward-paper candidate is recorded: V7 bidirectional / OKX / 4H / fixed 5% of initial balance. It returned 4,165.04 / 1,436.86 USDT in independent development / validation paths with 19.27% / 18.12% realized drawdown, but its 4H matched control was -0.155R with p=0.9747.
- Holdout-era configuration exposure is recorded as owner-authorized use #1. No production or notification configuration changed.
- Canonical source: `analysis/p1_spike_account_growth_20260913.md`; owner delivery: `analysis/html/p1_spike_account_growth_20260913.html`.
