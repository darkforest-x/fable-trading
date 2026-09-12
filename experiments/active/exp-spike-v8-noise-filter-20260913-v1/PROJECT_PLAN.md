# SPIKE V8 noise study — discovery frozen before new outcome review

Owner asked on 2026-09-13 to explain all 132,593 V7 admissions, reduce noise without discarding valid large trends, and implement a V8 research version. This is research authorization for this exact configuration and all dates already present in the frozen V7 cache. It is not permission to change the production monitor, notifications, ACTIVE model, thresholds used by other systems, or any live order setting.

## Population and chronology

- Frozen V7 cache: 3,531 authenticated Binance, OKX and Gate streams; 30m, 1H and 4H.
- Window: 2024-09-10 through 2026-09-10; 132,593 V7 admissions exactly.
- Development: 2024-09-10 through 2025-09-10. Candidate discovery may inspect outcomes here.
- Reused validation: 2025-09-10 through 2026-09-10. The discovery program must not summarize its outcomes. A selected rule and Pine implementation must be frozen before a later replay exposes this interval for V8.
- Existing data after 2026-05-04 has already been studied by V7 work and is not blind. For the eventual selected V8 configuration, the full replay will be recorded as holdout-era exposure 1.

## Discovery questions

The first pass changes one entry-quality variable at a time while preserving V7 raw confirmations and exits:

1. signed three-bar path efficiency at least 0.55, inherited from the original gradual-launch design;
2. current BB200 relative bandwidth rising from the prior close;
3. current BB width having left the prior-500 P10 compression threshold;
4. current volume ratio at least 1.5;
5. current true-range expansion at least 1.5;
6. original V1 same-bar hard impulse, RV at least 4 and TR expansion at least 3, reported as an inherited composite rather than disguised as one threshold;
7. round-trip cost no more than 0.25 of signal-close initial R;
8. close no farther than 3 ATR beyond the six-MA rope.

Continuous values are also reported by development decile with tie-aware AUC for net-positive and realized-10R outcomes. These AUCs describe ranking only; they are not the project success criterion.

## Failure taxonomy

All admissions remain in the signal table. A signal suppressed by an already occupied per-stream position is named `not_executed_occupied`, not mislabeled a failed trade. Natural trades are split into realized at least 10R, positive below 10R, initial stop within three bars, later initial stop, opposite-signal loss, trailing giveback loss, cost-dominated tiny-risk event, other nonpositive, and censored. Failure labels use frozen future outcomes only for evaluation.

## Selection gate before validation

Do not choose the rule with the highest in-sample return alone. Prefer a simple Pine-computable rule that, in every timeframe, removes meaningful signal volume while retaining at least 80% of exact realized-10R development trades. It must improve event PF or mean net return without relying on stablecoin cost/R artifacts. If no single variable qualifies, record that result before testing one explicitly declared two-factor conjunction. Never use first-per-compression or first-envelope-break as V8; the prior experiment proved both can consume a weak attempt and delete the later true launch.

After selection, freeze one V8 rule, retain unfiltered opposite V6 confirmations for exits, run the full serial execution/account engine, compare V7/V8 by period/timeframe/side/venue and matched random controls, render retained and missed large-trend cases, and only then decide whether the Pine version deserves use. No automatic production promotion.

## Development selection — frozen before reused-validation replay

The complete development screen found that same-bar volume, TR, V1 hard impulse, BB release and three-bar efficiency removed 25% to 93% of signals but retained only 0% to 83% of realized-10R trades depending on timeframe. They failed the every-timeframe tail-retention gate. The cost/R rule retained the tails but removed less than 1.2% of signals.

V8 therefore selects one rule: the confirmation close must be no farther than 3 ATR beyond the directional six-MA rope edge. In development this removed 13.86% / 14.28% / 12.07% of 30m / 1H / 4H admissions while retaining 136/143, 49/53 and 28/30 exact realized-10R trades. Event PF changed 1.136→1.147, 0.891→0.892 and 1.110→1.168. These are screening subsets, not yet a serial V8 replay. The selected rule, threshold and Pine expression are now frozen in `selected_rule.json`; validation outcomes have not been summarized.

## Fixed execution and costs

Signal close, next observed open entry, prior five-bar structure plus 0.2 ATR buffer, minimum 2 ATR risk, trailing distance 4 ATR after 2R, raw opposite V6 exit feed, and 0.2% round-trip cost remain unchanged. Independent stream accounts use 1% target initial risk and 1x notional cap; they are not a shared portfolio.
