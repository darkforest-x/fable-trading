# Fixed ETH V9 3m / 5m replay

## Owner authorization

On 2026-09-15, after the V9 full-pool and ETH 30m/1H/4H readout, the owner requested “3min 5min 也跑一下”. This authorizes the same frozen V9 bundle against V8 for existing ETH 3m/5m history, including the disclosed already-exposed 3m post-May-2026 segment. No parameter search or new venue/data fetching is planned. The earlier request to use subagents persists; a Luna Max mapper independently checks the parent path.

## Frozen scope

Use hash-bound indicator/raw-signal/mask contexts created by the earlier ETH martingale study solely as data caches; no capital policy, BE, martingale or position sizing is used. Source ETH 3m OKX covers 2023-07-31T11:12Z to exclusive 2026-07-30T11:09Z, split 2026-05-04. Source ETH 5m Binance covers 2020-01-01 to exclusive 2026-05-01, split 2024-01-01. Preserve original empty-position fold resets. Different venues and periods are not a causal timeframe comparison.

V8 must reproduce all original closed signals/trades by fold against run_20260913_v3, including 3m1460+111 and 5m1261+693 closed events. All newly replayed censoring is retained. V9 only ANDs the existing admission with spike_v9.entry_decision: exact base USDC / RV>50 / scheduled-next-open UTC Sunday; unknown values fail closed. Stop/trail/next-open/raw reversal, tick0.01 and round-trip0.002 remain fixed.

One deterministic random entry per actual event matches stream/side/signal month/fold/current ATR/close fixed bins; original events shared by both arms share their control. The fast fixed-entry path must agree with the original lowtf serial engine on real closed targets and synthetic paths. Missing or censored controls remain unmatched, never resampled. Monthly target-entry blocks, 2000 bootstrap/signflip replicates, are descriptive; no historical p-value is borrowed. Report all folds and counts, winner identity retention, costs and matched controls; no portfolio aggregation across the two streams.

## Exposure and delivery

This is the first V9 ETH-lowtf evaluation. The strategy already consumed the broad historical pool once; this extension is the second explicitly authorized historical evaluation of unchanged V9, with a first lowtf-specific consumption. The old V8 source exposure #3 remains recorded, not reset. ETH5m has no source rows in the project holdout era. No blind-validation claim; freeze/commit code, tests, config and authorization before loading market caches. Stop on source/parity failures, retain failure evidence, and never silently change engine or dataset to force parity.

Deliver analysis/p1_spike_v9_eth_lowtf_20260915.md and immediately render HTML; register a delivery manifest and Notion evidence. No TradingView edits, alerts, monitor switch or orders are required.
