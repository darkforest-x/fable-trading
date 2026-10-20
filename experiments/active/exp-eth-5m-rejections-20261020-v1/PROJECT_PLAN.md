# Fixed 5m entry: causal rejection diagnostics

Owner 2026-10-20 fixes entries to completed5m signals. Other timeframes provide context only; no15m/H1/H4 entry substitution. Continue ETHUSDT.P only. Preserve latest confirmed H1 watch, every later matching5m arrow, independent overlapping trades, five5m stop bars plus0.05percent buffer,15m close5R/4ATR trailing, armed oppositeH1 exit and20bp costs. This freezes the entry clock, not a new profitable default. No Pine/live change.

Reuse all4101signal and4101control native trades plus existing entry context. Only new5m features are calculated; no engine replay, downloads, hashes or dependency changes. No H4 warmup exclusion: all4101 remain the reference. Unknown range levels pass that veto and are counted, so missing context earns no filtering benefit.

Ten one-at-a-time rejection candidates, fixed before this study's outcomes; no combined search:
1. Reject cost greater than1initialR (initial risk less than0.2percent).
2. Reject cost greater than0.5initialR (initial risk less than0.4percent).
3. Reject a5m signal candle closing against direction or doji.
4. Reject a5m candle closing in the unfavorable half of its own high-low range.
5. Reject if at least2same-direction5m arrows occurred in the12completed bars BEFORE entry. This is density, not a one-trade-per-watch cap.
6. Reject if more than6hours elapsed since the latest confirmed H1 arrow.
7. Reject longs still below prior complete NY day low, or shorts above its high.
8. Reject longs still below current developing/frozen Monday low, or shorts above its high.
9. Require local sweep and recovery: reference low/high from12bars ending3bars before entry; last3bars must pierce it, and current close must recover across that same fixed reference. This tests a specific5m structural event, not every possible range reclaim.
10. Require %K<4 for long or>96.19 for short on entry or previous3bars, as previously specified by owner. This is a filter test, not an implied change to baseline.

OHLC, Stoch and reference levels are closed at entry; no future labels or later realized trades affect masks. Window lengths are fixed, not optimized. Test controls with their own side/time features then rematch within original segment/week/side/causal volatility bucket without replacement, using existing evaluator; report coverage. Original controls are diagnostic, not a tradeable strategy or an exhaustive random universe.

Also describe original signal ordinal within H1 watch, watch age, cost/R, repeat density and MAE-to-stop speed (outcomes only). Ordinal counts raw eligible5m signals since the watch, never filtered fills. No last-stop or prior-trade outcome filter. Retain all decisions and gross10R/20R false rejections; peakR is separate from realized grossR. GrossR milestones are not net after costs.

Use2024 for selection only: >=100trades,>=20watch events,>=80percent retention of both gross10R/20R winners, and greater mean net basis points than baseline. Rank eligible single vetoes by development meanbp. Evaluate this frozen choice on2025 and2026 individually and combined. These are previously examined histories, not untouched validation. Keep all failures; no extra masks after results.

Report meanbp and independentR, losses removed, winners removed, tail retention vs trade retention, matched controls, and weekly clustered confidence intervals for each filter-minus-baseline meanbp (all signal trades, unequal weekly sample sizes). Holm across10candidate contrasts; separately retain matched-excess tests. Excluding trades can lower losses without improving expectancy; statistical improvement is not positive profitability. AUC/top-decile ranking are inapplicable to these fixed binary admission rules, no trained scores exist.

Commit builder/config/tests before generation. Focused tests cover prefix causality, mirrored candle/sweep semantics, no future-outcome mask dependence, current arrow excluded from repeat history, watch refresh and matching reuse from existing test suite. Raw outputs local; compact summaries/receipts registered. No unsolicited report.
