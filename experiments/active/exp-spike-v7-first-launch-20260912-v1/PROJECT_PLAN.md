# V7 first-launch episode study — frozen before outcome replay

Owner approved subagent exploration of the proposed V7 first-launch mechanism on 2026-09-12. Existing all-date authorization applies. This reuses previously studied history, including dates after 2026-05-04; it is not blind holdout. This configuration's first complete return exposure is run 1. No Pine, production, monitor, notifications, model training or orders will change.

## Scope and unit

Use the same 3,531 authenticated Binance/OKX/Gate 30m/1H/4H streams as exp-spike-exit-policy-20260912-v1. 2024-09-10 to 2025-09-10 is development; 2025-09-10 to 2026-09-10 is reused validation. Both directions. No data fetch or outcome-based symbol selection. Full signal replay, with independent per-stream accounts at 1% target risk and 1x entry notional cap. These are NOT shared-capital portfolio returns.

## Fixed ablations

- B / baseline: frozen original V7 admissions and original raw V6 reversal feed.
- A / first: first original V7 signal of either direction per BB compression episode. A published admitted signal consumes its episode even if an existing position prevents a new fill.
- C / first_break: first original V7 signal closing above the episode high (long) or below its low (short). Rejected in-box signals do not consume C's episode. C does not manufacture a signal on a later bar without an original V6 signal. No new same-bar six-MA rule: existing V6 structure acceptance is retained.

An episode is formed by a completed 3-bar BB compression run using existing BB200, 2-sigma, prior-500-bar P10. Subsequent completed 3-bar runs connect to the same episode if their end is no more than 10 bars after the previous qualifying end, reusing the original V7 recency span. Otherwise they start a new episode. The envelope uses highs/lows of compressed bars that belong to qualifying runs, updating causally; each decision uses only the envelope known at the PREVIOUS close. In non-compressed bars it stays frozen; renewed qualified compression within the episode may update it for future decisions. This is a compressed-price envelope, not a retrospective range enclosing every intervening candle.

Long and short share one consumed flag per arm. A versus B isolates first-per-episode; C versus A adds price escape but can choose a later original signal, so it is not necessarily a subset of A. Raw V6 reversals always remain available to exit a held opposite position regardless of entry rejection.

Caches start 120 bars before the evaluation start. The initial compressed episode is left-censored if no full reset was observed. In that case all arms retain B admissions until a complete episode boundary is observable; log this fallback, do not silently drop uncertain cases. Gaps reset state and no envelope bridges a gap.

## Execution and comparison

Keep existing signal-close/next-open execution, prior 5-bar structure plus 0.2ATR buffer, minimum 2ATR risk, 4ATR trailing after 2R, raw-V6 reverse exits and 0.2% round-trip cost. Reuse the verified exit engine by replacing only the V7 admission mask in an isolated in-memory cache. For every stream B must match the prior ledger on closed trade identities/prices/outcomes before a completion receipt is written. Synthetic prefix, gap, boundary, direction and reversal-feed tests precede outcome replay; commit builder first.

Prespecified exploratory target for 1H and 4H separately: >=30% fewer admitted signals; >=80% of original closed realized >=10R trades retained at the exact original entry; positive paired mean account-return difference in BOTH development and reused validation. Report losses removed, all winning and losing outcomes, realized versus MFE tails separately. Same-episode alternative entries are supplementary and not counted as exact retention. No guarantee of 80%, no automatic acceptance/deployment even if these descriptive targets pass.

Matched random event reference: deterministic maximum one naturally closed target per stream/arm/side/year, selected by SHA256 of signal time and side (not outcome). Match same stream/side/calendar month/causal prior-120-bar ATR-percent quartile, same V7 history readiness, exits and costs; seed 0. Exclude a control whose same-bar opposite raw V6 signal would contradict the held side. Use the existing matched-control implementation and report incomplete matches. This sparse diagnostic is not a shared-account null or proof of profitability. Cluster account deltas and control excess by base asset, keeping cross-venue duplicates together. No AUC or top-decile ranking is applicable to the fixed unscored gate; provide paired baseline and matched event nulls instead.

## Deliverables

Reproducible runner, synthetic tests, hashed per-stream results, all-stream count/parity receipts, account/event/signal/retention tables separated by period, explanatory example charts for retained winners, mistakenly removed winners and removed losers, Chinese Markdown report converted immediately to self-contained HTML, immutable learning and experiment/artifact registry entries. Report reused-data, independent-account, missing funding/slippage/mark-price and close-only-drawdown limitations. No parameter search after seeing outcomes.
