# Stoch/WVF entry experiment, frozen before results

Owner 2026-10-19 authorizes separate Stoch oversold-cross and same-bar WVF-green backtests, plus Spike pullback entry timing. No parameter search. Research only, no Pine/production/order changes.

## Input and unit
Reuse registered 638 Binance USD-M historical USDT identities, 15m/30m/1H/4H. Nov2022 warmup, Jan2023 analysis start, Jan2025 chronological cut, end Sep23 2026 04:00UTC. Long-only queues: cannot compare their aggregate directly to earlier combined long/short queues. History reused and universe/ticks not point-in-time. No downloaded or hashed data.

## Frozen arms
Standalone Stoch5/3/3: K crosses above D with both below20, next open. Combined: additionally require WVF green on the same decision bar. WVF22/20/50, multiplier2, highest-times0.85 unchanged. Admit only prepared history and50 uninterrupted chart bars, nonSunday UTC closes. Standalone means independent entry admission; both share original Spike raw opposite exits and original risk engine, not newly tuned oscillator exits.
Spike comparison: original rebreak, original first valid pullback, first Stoch cross at/after first valid pullback, and first combined cross at/after that pullback. Breakout precedes retest; retest holds original anchor high on close. After retest, original gap/raw-opposite/stop-touch/lost-level cancellations continue. Total deadline remains24 bars from anchor; no eventual-confirmation conditioning. No qualifying cross means no trade, not delayed unconditional entry.
All arms: original5bar stop/0.2ATR buffer/2ATR floor, actual-entry risk units, close2R activation, next-bar trailing1H2ATR otherwise4ATR,20bp round trip. Spike absolute stop stays at original anchor. No added red-arrow exit. Each arm replays its own serial long-only occupancy.

## Controls and estimands
Fixed ID-seeded same-symbol/long/UTCweek/timefold/ATR-price-bin random anchor; no redraw after outcome or Sunday/continuity failure. Spike preserves earlier random identities. Both standalones use the same original Stoch opportunity list and random draws; WVF restriction selects both members of a pair by signal-anchor WVF, so paired incremental selection is defined on common opportunities. A stricter supplemental standalone WVF control requires both anchors green and rebuilds queues. Spike applies identical wait state machines to each random anchor. Unknown required outcomes excluded; skipped trades and known nonfills zero. Full and paired populations reported separately. Queue warmup precedes analysis.
NetR, count, meanR, PF, realized closed-R drawdown and weekly blocked paired excess. Also4-calendar-week block sensitivity. Holm correct later timeframe tests across all prespecified contrasts. Statistical significance alone cannot bypass absolute net profitability, stable time slices or controls.
MAE/MFE measured to actual fill, grossR and basis points; stop-bar favorable wick gets lower/upper bounds. Compare original and variant on common realized entries as descriptive conditional diagnostics; also all-opportunity netR with zeros, fill/holding exposure and missed baseline5R+ winners. Never call fewer trades lower per-trade loss, or smaller R denominator better price.
Prespecified contrasts: standalone Stoch vs matched random; standalone combined vs matched random; combined-minus-Stoch incremental excess; Spike pullback vs rebreak, pullback-Stoch vs pullback, pullback-WVF vs pullback-Stoch. Also direct netR/MAE comparison of each timed arm to rebreak. No selection by results.

## Verification and delivery
Commit builders before data execution. Synthetic decision tests, signal formula existing tests, causal prefixes, full original baseline path parity, independent scalar/serial/membership audit and raw extrema. Validate sample counts and preserve failures. No standalone report: raw ledgers, compact JSON, registry/Notion updates and conversation conclusions. Production/training false. Subagent requested Luna max but runtime cannot confirm actual model, interrupted before research; parent executes.
# Owner scope amendment (2026-10-19)

Owner requested "460 is enough" after the 460/638 progress update. Stop the
remaining path generation and use exactly the first 460 completed symbol
receipts ordered by completion mtime, then symbol. Nine additional symbols
finished before termination; exclude them without inspecting returns. Preserve
the interrupted full directory and freeze a linked subset in `subset460_v1`.
This is an operational completion subset, approximately dispatch order, not a
random or representative universe. The six policies, costs, exits, chronological
split and inferential family remain unchanged. No policy returns were inspected
when the owner selected this sample size.

