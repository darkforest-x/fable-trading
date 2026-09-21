# Continuous profitable pyramiding: fixed first research protocol

Owner authorization 2026-09-21: annotation selecting the rule “new completed1h pullback breakout, raised common stop, whole-position profit/risk and collateral admission” and request “研究一下”. This authorizes offline research of this hypothesis, not a trading-account change or production promotion. The previous arbitrary two-add limit was an explanatory assumption, not an optimized result.

## Identification and scope

The screenshot shows WIFUSD1h, reference entry0.1402, stop0.1362, high0.2296, but venue/date/exact signal remain unknown. Initial read-only inspection of existing OKX15m through2026-08-14 and Binance15m through2026-04-30 did not identify the full pictured episode. Do not silently call a proxy the screenshot trade.

Use exactly the prior29 Binance sources in `exp-spike-v9-htf-sma-20260920-v1/run_v1/identity.json`, one1h stream per symbol, same raw SHA and archived tick/base metadata. Published V9 long signals are a clearly labelled management research proxy. These include all long outcomes, not only later large winners. Freeze dates2024-09-10..2026-05-01 exclusive, time split2025-09-10; no train/validation random split. Prior-studied later data is nonblind temporal replication. All historical data is permitted; no holdout accounting.

## The single comparison variable

Three arms differ only by successful add count limit: none, two, unlimited. Each uses the same initial entry, frozen original initial stop, raw opposite exit, original close2R/4ATR trailing protection, and an additional common close-confirmed pullback structural stop. Thus this is NOT a claim that the new structural stop improves original V9. It is a comparison of addition counts UNDER one common proposed stop policy.

Structure clock is independent of fill history: a lower close starts a pullback and freezes the preceding running high; the pullback low is accumulated through the later close above that high; only then may stop ratchet to one archived tick below that low. Every structure event resets the clock in every arm, including arms unable/unwilling to add. No future pivots and no new stop tested against the already elapsed current-bar low. Entries/adds execute no earlier than next open; active protection beats pending reversal, which beats an add. Each event is consumed once, whether accepted or rejected. A gap censors rather than invents a fill.

First and subsequent additions require price above the previous actual fill, completed close at least2 original priceR above entry, and a strictly raised stop on a new structure event. Preserve the initial R, never recalculate R from moving average entry. Quantity is the lesser of available stop-profit budget and cash/exposure headroom. At least50% of the newly available stop-scenario profit is retained; previous committed stop-profit cannot be worsened by an add. No arbitrary50%/25% initial-quantity schedule: the same new quantity function is used by both adding arms. The study isolates count limits, not the old illustrative sizing.

## Capital and costs

Method benchmark: each event begins with100 units and targets1 gross unit of original stop risk, subject to a1x cash-backed exposure ceiling. No borrowed funding or liquidation claim. This preserves a finite funding constraint without inventing historical maintenance margin or mark prices. These are measurement units, not Owner portfolio settings. If the risk cap binds, record actual initial dollar risk; all R outcomes divide by that shared actual risk.

Keep the repository fixed20bp round-trip basis: each leg pays0.002 times its ENTRY notional. The earlier conversation's illustrative0.1% of both execution notionals is a different arithmetic basis and is not reused as benchmark. Funding, slippage, discrete contract size and market impact remain unmodeled; report limitations. At100units1gross risk, summed event-R is not a compound account or a portfolio; cross-symbol overlap prevents that interpretation.

## Sampling, controls and inference

One serial long position per symbol per period; signals while occupied are ignored identically across arms because quantity cannot change exits. No position spans the temporal cut: unresolved boundary positions are marked/censored separately, and later simulation begins flat. Random long entry for every event matches symbol, scheduled UTC month, temporal segment and current ATR/close bucket[0.005,0.01,0.02,0.05,0.1], with original ready/RV/calendar guards. Exclude every true V9 long target timestamp, including targets skipped while occupied, before drawing: the null is eligible non-signal timing. One deterministic draw, no outcome-dependent retry; shared across all three arms; duplicate random timestamps are reported and not called independent samples.

Run each draw under identical stop/fee/sizing rules. Censored and unavailable matches remain explicit. Inference uses shared calendar-month blocks across symbols. Predefine later continuous-vs-two and continuous-vs-random one-sided monthly sign-flips; Holm-adjust the two claims. Earlier results are descriptive. Other count comparisons are diagnostics, not an optimization search.

Required report: candidate/entry/closed/censored counts, dates, initial-risk/quantity clipping, gross/net R and initial-notional return, net win rate, PF, close-marked intratrade drawdown and peak giveback, add count distribution, >=3add paths, net profit deterioration after extra adds, WIF subset, chronological segments, paired random controls and monthly confidence intervals. AUC/predictive top-decile/feature selection are inapplicable because no predictive score is fitted; state this and use the strict paired-policy and matched-timing nulls. Do not replace them with a retrospective top-decile winner claim. No automatic adoption even if a p-value passes.

## Gates and deliverables

Commit unchanged builder, engine, tests, config and protocol before market replay. Verify sources and transitive local dependencies. Focused causal tests include gaps, opening stops, raw reversals, stop update visibility, future mutation, >=3add synthetic path, whole-position floor, fee conservation and finite funding. Market integration asserts identical three-arm initial/exit identities, prefix signal parity against released V9, same pre-third-add decisions for two/continuous, complete29 coverage, and source hashes.

Deliver immutable run_v1 event/control/fill ledgers, summary/statistics, `analysis/p1_winner_pyramiding_20260921.md`, registries, learning and Notion. No HTML/Pine/alerts/account operations, training or promotion. A source/file mismatch is an error, not permission to silently substitute.
