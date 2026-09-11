# V6 + BB200 squeeze: fixed incremental comparison

## Scope and single-variable sequence

Owner supplied ChartArt RSI6 + BB200/2sigma code and asks whether extreme channel compression improves V6 on 15m/30m/1h/4h. Test A (common-ready V6), B (recent compression), C (B plus expansion), D (C plus RSI directional level), with A0 full V6 reported only as coverage reference. Each adjacent step adds one condition. Do not import the original mean-reversion entry orders. Defaults are declared mechanical definitions, not fitted optimal parameters.

## Causality and readiness

Width is (upper-lower)/abs(SMA200), ddof=0. Compare each width with the 10th percentile of the preceding 500 widths. Qualifying setup has a full 3-bar compressed run inside the 12 bars preceding the V6 signal. The current breakout may widen the band; it need not stay below the threshold. No waiting for a future confirmation or moving a past arrow. Gap resets all rolling state. A/B/C/D share feature-ready opportunity timestamps; A0 exposes coverage lost to warmup, especially new 4h markets. Shorts use symmetric compression and directional RSI, not mirrored prices.

## Data and immutable baseline

Use the same eight fixed OKX markets and UTC folds as the WVF experiment. Reuse committed cached V6 features and events for 30m/1h/4h; identify real 15m inputs separately, never split 30m candles to invent lower-timeframe data. Freeze source manifests before outcomes. Missing history remains explicit. Existing and new cached histories are research-only, authorized by owner, not blind holdout.

## Execution and evaluation

All policies preserve original unfiltered V6 reversal exits and the same frozen SL/trailing/cost next-open replay. Recompute each policy's single-position history; a static deletion of the original trade book is not a backtest. Compare by fold, timeframe, side and coin: raw/admitted/executed counts, net win rate/PF, realized netR, actual same-entry >=10R retention versus MFE, failure exclusions and deleted winners. Show independent stream account return and closed-trade DD with its MTM limitation; no sum of trade returns labeled account profit. Include verified 99-seed matched random-entry controls, and representative retained-success/rejected-failure/missed-winner charts.

## Acceptance and provenance

Before outcome generation, freeze random-control eligibility to the same BB/RSI-ready timestamps as A. B/C/D use this common opportunity null; controls are not required to satisfy the added squeeze/expansion/RSI gates. Control positions are independent matched events, not an investable random portfolio.

Commit all evaluation builders before formal generation. Tests must cover lagged compression thresholds, scale invariance, finite warmup, gap reset, setup expiry and no future dependence, plus reference execution tests. Register final report and receipts; convert report Markdown to self-contained HTML. Preserve negative findings. AUC/top-decile ranking and ranking permutation p do not apply to this non-ranked rule comparison; matching controls provide the economic null without claiming independent-seed statistical significance. No Pine/TV/monitor/Bark/order/training changes. Main only, no worktree, preserve other tasks.
