# SPIKE V9 vs V8 full frozen-pool replay

## Owner authorization, 2026-09-15

After V9 implementation, the owner explicitly requested: “用子代理 好了之后 v9保存到tv 跑一下v9全量回测 对比一下v8”. This supersedes the preceding no-comparison scope for this new experiment. It authorizes saving the fixed V9 source as a private TradingView script and one full comparison with the original V8 pool, including its already-exposed holdout-era rows. No new parameter search, deployment, orders, notification creation or unrelated model promotion is authorized or needed.

## Fixed comparison

- Universe: the same 3,531 Binance/OKX/Gate perpetual/timeframe streams as the frozen V8 report, 30m/60m/240m. Preserve source receipts and all failed/missing coverage.
- UTC entry range: [2024-09-10, 2026-09-10); split at 2025-09-10. No new data fetching or expanded pool.
- V8: receipt-bound native serial baseline; require full per-stream ledger parity, including censored rows. Original report reference: 94,746 closed trades, net -2,021.78R, 532 realized net >=10R.
- V9: frozen source ef4de009d068c30bba33166b52423e53d26d1781; combine exact-base USDC, current confirmation RV>50, and scheduled-next-open UTC Sunday exclusions. Unknown inputs do not pass. Never screen reversals from the raw V6 exit feed.
- Preserve original five-bar stop with 0.2ATR buffer/2ATR floor, close2R-activated4ATR trail, next-open entry/reversal, intrabar pre-existing stops, gap/boundary censoring, 0.002 nominal round-trip cost. No MFE/BE treatment, portfolio sizing or aggregation into account profit.
- Full serial rerun, not posthoc deletion. Separately show removal/re-entry attribution and original 10R identity retention.
- Report all/full, development and later-year periods, timeframe/venue/asset/month attribution, R-based PF and event-sequence drawdown. Disclose censored rows and cross-split exclusions. No AUC/ranking metric for an unranked deterministic gate.
- Matched random entry controls use same asset/time block/volatility bucket, side, barriers and costs with a fixed seed if existing verified controls are suitable; selection diagnostics are separate from market-entry excess.

## Holdout and scope receipt

This is V9 configuration's first full historical evaluation/holdout consumption (#1); historical samples were previously exposed in V8 studies, so this is not blind OOS. Recomputing unchanged V8 for parity is recorded as a baseline reuse in this comparison, not a newly tuned configuration. No changes may be selected from these outcomes. Any implementation failure is recorded; identity changes require a new output directory, not overwriting the failed run.

TradingView save/compile is a technical delivery. Saving does not authorize public publication, alerts or trades. A chart preview, if required for compilation, will be recorded separately and not scored as additional backtest evidence.

## Delivery gates

Commit new runner/tests/config before market evaluation. Synthetic tests must show new mask parity, raw reversal preservation and no future inputs. New per-stream outputs must be resumable and hash-bound; aggregate only successful receipts and require all 3,531 before claiming full coverage. Deliver analysis/p1_spike_v9_full_backtest_20260915.md and its immediately-rendered HTML; register manifest, learning and Notion results with an honest financial verdict.
