# SPIKE exit policies and account risk — frozen research plan

Owner request (2026-09-12): default 1R/2R/3R chart levels for V1/V6/V7; test account risk 3%,5%,10%, break-even at 0.95R/1R, partial exits 25% at1R and35% at3R, and causal early exits; seek the strongest net performance. All historical dates previously explicitly authorized. This request authorizes these research execution/obstacle changes and the explicit partial-plus-break-even combination; it does not authorize real orders or changing production notifications.

## Design fixed before this experiment's outcomes

- Input: exact authenticated control caches from exp-spike-v7-v1-compare-20260912-v1 replay_two_year_20260912_v3, 3531 market/timeframe streams, Binance/OKX/Gate, 30m/1H/4H. No outcome-selected pool, no source refresh, no old artifact edits.
- V1 uses archived common-execution long signals, V6 and V7 use their frozen both-direction admissions. Native original V1 is not relabelled as this execution model or as short-capable.
- Development: confirmation timestamps 2024-09-10..2025-09-10; validation: 2025-09-10..2026-09-10. Calendar account paths inherit open positions across the split; separate event tables disclose entry-time attribution. These dates were already researched, so validation is chronological reused research, never a blind holdout.
- Baseline: next-open entry, frozen recent5 extreme/.2ATR buffer/min2ATR stop, 2R arm4ATR trail, raw opposite V6 next-open exit, .2% original-entry-notional roundtrip costs.
- Policies: baseline; no_reverse; be095_price; be1_price; be1_cost; partial_1_25; partial_1_25_3_35; partial_be1_cost; fast_ma_exit; md_cross_exit.
- Break-even/partial thresholds are CLOSE-confirmed. Updates/market partials effective at the next open. A bar's old protective stop takes priority; no same-bar newly armed stop backfill. Fractions refer to original quantity. Cost break-even uses entry +/- .002*entry for the remaining unit; does not guarantee actual net-zero fills in gaps. fast_ma exits beyond both SMA20/EMA20, md_cross exits on opposite md/sb crossover. All other baseline exits retained except no_reverse.
- All policies replay the complete signal sequence, so changed holding periods can change subsequent executable entries. Do not merely replace old trade returns.
- Single-variable comparisons against baseline plus owner-requested partial/BE combination. No data-driven new thresholds after outcomes.

## Capital assessment

Per-market/timeframe independent hypothetical accounts: initial 10000 quote currency, target initial gross stop risk1% (reference),3%,5%,10% of equity, quantity frozen at entry; partials do not reset R or re-risk. Main table caps entry notional at1x equity. Separate uncapped mathematical stress table exposes leverage requirements/ruin rather than presenting them as achievable returns. Fees funded at entry/exit; mark-to-market close equity and drawdown include remaining fractions. Mark history comes from source bars. Gap censor => invalid account valuation, not zero return; boundary mark disclosed as unrealized/estimated closeout, not actual execution.

No cross-market synthetic compounding. Aggregate summaries are distributions of independent accounts; duplicate underlying across venues/timeframes are not independent discoveries. No claim of actual futures liquidation modeling: mark prices, maintenance tiers, funding, depth and exchange order size restrictions are not fully in these caches.

Policy selection before validation: within each signal variant/timeframe rank mean development-period ending wealth of equally endowed1x capped independent accounts at1% risk (zero-entry accounts included; invalid accounts excluded with count). Tie-break lower development drawdown then fixed policy list order. Freeze selection before ranking the second year's results; display every policy for transparency, not relabel the validation maximum as prespecified. Risk fractions compared separately for both baseline and selected policies. Mean wealth can be dominated by tails; report median, P10/P90, top asset concentration and per-venue/year results.

Metrics: closed-event win rate, netPF, netR, net≥10R, MFE vs realized, partial/BE/early exits, original-entry matches, independent account net returns/close MDD/ruin/leverage. For exit effect the unchanged entry-stream baseline is primary paired control; existing matched-random baseline results remain referenced, and no novel entry edge is claimed without new matched controls. AUC/top-decile ranking metrics do not apply to hard-rule events.

## Engineering and provenance

Engine source and test parity before full outcomes; source commits/hash, input receipts, retry history and runtime logged; atomic per-stream resumability; use at most one CPU research worker by default to protect Mac temperature. Parent owns config/plan, capital/post/report/registries. Agent owns engine and tests; another agent owns Pine display only. Frozen old feature/replay sources remain unchanged.

MD+HTML final delivery with negative outcomes, limitations and reproducible commands. No promotion, model training, production rule/sizing change, orders or notifications.
