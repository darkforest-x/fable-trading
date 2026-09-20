# Frozen simple-trend baselines against Spike

Owner authorization: 2026-09-20, after the assistant proposed a same-universe/time/cost/comparable-risk comparison of simple breakout, moving-average and Spike systems, the owner replied “去做吧”. No new risk parameters, parameter search, production changes or Pine delivery are authorized or needed.

## Question and scope

Compare the categorical **signal family** while retaining the existing fill, initial-stop, trailing-stop, costs and serial position contract. Signals also own opposite-direction exits, so this is NOT an entry-only causal ablation. The engine's historical `opposite_v6_next_open` reason is relabeled `opposite_signal_next_open` in the new artifacts for simple families.

Arms: original V9; V9.1 (owner-selected completed H1 SMA60 gate on15m only, other periods unchanged); close beyond previous20-bar or55-bar high/low; SMA20/60 crossing. Repeated new channel breaks may signal again after a stop; moving-average crossover requires a new cross. Equal moving averages and exact channel touch do not signal. Simple arms are common-risk chart-bar adaptations, NOT the complete original Turtle rules. Classic day-based systems, pyramiding and their distinct exit/risk rules are outside this experiment.

Use exactly the prior29 frozen Binance USDT perpetual sources, with metadata and each symbol's raw SHA checked against the completed V9 HTF-SMA run. PEPE maps to1000PEPE; ARY remains excluded. Do not fetch or substitute sources. Dates2024-09-10 to2026-05-01 exclusive, split2025-09-10. Existing later history is already studied, not blind OOS. Keep cross-split and boundary/gap-censored positions separate.

All families use original V9 calendar/RV/base guards and a common readiness clock:61 consecutive valid OHLC bars plus original feature readiness. Simple features reset on invalid/missing chart bars. Existing chart aggregation/ATR semantics are preserved for parity, including partial buckets; count and report them. Raw exit signals remain independent of admission guards. The H1 gate additionally requires60 completed H1 candles and has explicit availability counts.

Risk and costs remain five-bar extremes plus0.2ATR/minimum2ATR, close2R arming4ATR trail, next-open entry and20bp roundtrip. No fixed profit target, new stop multiple, cost discount, real positions or leverage settings. A one-R event curve is not an account; report event-R drawdown and time below its previous high with that limitation. Same initial-risk formulas do not guarantee equal aggregate concurrent risk or equal notional.

## Controls and inference

Each executed event gets one deterministic random entry matched by symbol, direction, UTC calendar month, time split and current ATR/close bucket. Eligibility obeys common readiness and guards. For a common signal time/side the draw is shared, but each family evaluates the draw with ITS OWN raw opposite-exit stream, stop and costs. Never deduplicate control outcomes across families. Reject unavailable draws without outcome-dependent redraw; report unique control timestamps and unmatched/censored/cross-split counts.

Report candidate/entry/closed/censored counts, net/gross R, fixed-notional bp, net win rate, PF, cost-R, closed-event drawdown/duration,10R counts and share of positive profits, top10percent realized-profit concentration (post-outcome diagnostic, not predictive top-decile alpha), monthly and per-symbol/direction results. No trained scores: AUC and predictive top-decile statistics are inapplicable; the simple rules are the single-family baselines.

Month-block bootstrap/sign-flip tests preserve same-month cross-symbol dependence. Show paired random excess with uncertainty and simple-arm differences against V9. Nine later simple-arm/timeframe tests receive Holm adjustment. With eight months, p resolution is insufficient for0.01 after adjustment; show this upfront, do not loosen the threshold or assert a validated winner. The purpose is exploratory diagnosis and a reproducible baseline, not production acceptance. Keep all failures and do not retune after seeing results.

## Verification and delivery

Commit unchanged builder, signal functions, tests, config and plan before replay. Bind transitive repository source hashes and old receipts. Reproduce original V9 and previously tested15m SMA60/V9.1 trades against old receipt-bound actual-scope ledgers for every stream before interpreting new results. Verify future-prefix invariance, missing data, exact crossings, serial stop/reversal order, fixed-entry versus serial outcome consistency, cost arithmetic, cross-split control handling and complete29x3 coverage. A small synthetic test is not a market build.

Deliver analysis/p1_trend_baselines_20260920.md, immutable run_v1 ledgers/controls/receipts, statistics and registry entries. Update HANDOFF, preserve prior reports, save findings to Spike Notion with honest evidence status, and record an extract-approach learning. No automatic HTML, Pine modification, training or promote.
