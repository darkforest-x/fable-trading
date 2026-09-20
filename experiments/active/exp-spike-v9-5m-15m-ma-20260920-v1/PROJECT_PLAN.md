# Five-minute V9 entries with fifteen-minute moving-average direction

Owner asks on 2026-09-20: 验证一下，5min级别开多空，应该看15min级别哪条线最好. This authorizes a research comparison, not changing TradingView defaults or live settings. The triggering ETH OKX 2026-08-19 loss was already inspected, and is not a selection or validation sample.

## Fixed question and arms

Compare the six existing chart lines: close SMA/EMA20/60/120 and no filter. The treatment is one categorical admission variable: which15m line permits the existing5m V9 signal. No slopes, multiple-line combination, body-cross rule or extra threshold is added. Long requires5m signal close above the known line, short below; equality and unknown reject. Both directions share a line in the primary comparison. Direction-specific results are subdivisions of each complete two-sided replay, not a claim about a newly mixed long/short policy.

Use the prior frozen29-symbol Binance USDT perpetual5m archive and prior source hashes; report ETH separately. Universe preference was requested from Owner; default29 is retained absent a correction. This cross-market primary comparison is intentionally broader than the single ETH screenshot. No fresh exchange data substitution or download is needed. Period2024-09-10 through2026-05-01 exclusive; split2025-09-10. The later period has been studied before and is temporal validation, not untouched blind data. Historical data access is unrestricted under Owner's latest rule; the finite source window is a reproducibility choice.

## Causal definition and nuisance controls

Build15m candles only from all three valid constituent5m OHLCV rows on the UTC epoch clock. A15m close is visible only when its close_time is at or before the5m BAR OPEN. This matches the prior published offset-inside-security convention. A5m signal ending on a15m boundary does not use the just-finished15m until the next5m bar. Missing or invalid constituent bars invalidate the higher bucket. No stale higher candle crosses a missing bucket.

SMA uses N consecutive completed valid closes. EMA uses alpha2/(N+1), seeded by the first finite observation in each contiguous segment, consistent with the repository's Pine EMA recurrence on continuous data. Require10*N valid15m bars before EMA admission so seed influence is negligible; gaps restart that history. This research continuity policy is disclosed and does not assert full native Pine parity.

Primary scope requires all six lines ready, including1200 completed15m bars for EMA120. Every primary arm including no-filter uses the same readiness clock. Secondary actual scope uses each line's own readiness, preserving the original ungated V9 baseline. Show warmup/gap exclusions explicitly. Identical masks may reuse the same immutable computed path with only scope metadata changed.

## Execution and evaluation

Every arm runs the existing full two-sided serial engine, one position per symbol. Preserve raw opposite signals as exits even if their new entry is rejected. Preserve next-open entry, recent5bar extremes with0.2ATR buffer and minimum2ATR risk, close2R activation and4ATR trail, and0.2percent round-trip cost. Filtering ledger rows is forbidden because occupied positions change subsequent opportunities. Check no-filter admissions and complete trades against the original V9 adapter for all selected symbols.

Select exactly one shared line (or none) using development total netR on the primary common clock. Exclude cross-split and censored trades. Ties follow the fixed arm order. Do not reselect based on validation, the full-history highest result, ETH alone or the screenshot. Show cumulative gross/netR, nominal mean/sum bp, win rate, PF, closed/censored counts, event-curve drawdown and original10R winner retention. Event-curve R is not an account equity return.

Match random entry controls within symbol, side, month, time fold and ATR/price bucket, with identical risk, exit and cost rules. Seed920515 and event keys are fixed across arms. Keep unavailable, cross-split and censored controls; never redraw because of an outcome. Provide every grid result with matched-random context and pair counts. AUC and top-decile metrics are not applicable because there is no classifier or ranked score; the no-filter baseline, matched entries, and shared-month sign-flip nulls test the actual intervention.

Use shared calendar months to preserve marketwide dependence:4000 bootstrap draws for baseline-difference95percent intervals, exact sign-flips over the eight later months. One pooled primary selected-line test has attainable minimum p=1/256, unlike the previous three-test Holm design. ETH/direction/grid rankings remain explicitly exploratory. Accept only the fixed config intersection (positive later netR, positive R and nominal delta, positive lowerCI, p<0.01, tail retention, same-direction actual-scope improvement); otherwise report the best development line and its validation failure. Passing is research evidence only.

## Deliverables and checks

Commit builders, config, plan and tests before producing market artifacts. Targeted checks cover15m visibility boundaries, mutated future suffix, invalid/missing buckets, scalar EMA recurrence, unknown/equality, reverse-exit preservation, trade/fill/cost parity, temporal selection and cross-split controls. Run with three symbol workers and hash every source/output with receipt-bound completion. Report all outcomes in analysis/p1_spike_v9_5m_15m_ma_20260920.md, preserve complete CSV evidence under this experiment, register the experiment/artifact, write a learning and capture the result in Spike Notion. No HTML or new Pine delivery is requested.
