# ETH4h R2 autonomous bounded exploration — frozen before outcomes

Owner: “你试试吧 开启目标模式 自己探索”, 2026-09-07, in response to the proposed BE-on/off experiment and slow-MA slope entry gate. This authorizes those research changes. No change to initial hard stop min(4ATR14,3%), fee0.001 each fill, 0.5% planned equity risk,1x cap, original entry oscillator thresholds, cooldown thresholds, live execution or holdout. No other numerical optimization.

## Hypotheses and adaptive sequence

1. Development2021–2024: reproduce R1 and change only BE enabled→disabled. Preserve initial protection, same-side tightening and opposite close-only exit. Estimate full-portfolio outcomes and a separate common-signal counterfactual cohort, so changing opportunity occupancy cannot masquerade as a per-trade exit effect.
2. Select the BE-off parent only if ALL prespecified development conditions pass: at least20 closed trades; positive account return greater than R1; mean net trade return greater than R1; matched excess positive and not lower than R1; OHLC-path drawdown<=20%; at least as many profitable calendar years as R1; no insolvency. Otherwise keep R1. This is an economic research screen, not statistical or production acceptance.
3. On that deterministically chosen parent, change only flat-entry eligibility: side*(SMA60[t]-SMA60[t-1])>0. No slope lookback or threshold search. Original raw signals still count cooldown and manage open positions; the gate never suppresses an opposite close or protective tightening. Controls require the same directional flat-entry gate.
4. Apply the same development conditions against the chosen parent. Save both decisions before reading any new2025+ outcomes. Final candidate is fixed at this point. Stop this iteration after these two hypotheses; do not invent more parameters based on replication.
5. Report all three strategy policies (R1,BE-off,slope-on-chosen-parent), plus final-policy SMA-only entry comparator, in independent exposed2025-Apr2026 and continuous2021-Apr2026 windows. Report all failures. Do not change the chosen candidate after seeing replication. Subsequent work must be separately registered.

## Evidence and sources

Only fixed-hash Binance USD-M ETHUSDT15m file through April2026, SHA07013f996a13a0b205f9df6df2af5deab5d601e7e34b42bfc308dc13aef400bd. Complete16-bar UTC4h aggregation;2020 warmup. No >=2026-05-04 holdout reads, fetches or scores. History including2025+ is already exposed and never called unseen.

Same event-cost protocol and independent500USDT accounts as R1; administrative last-close exit at each window boundary including commission. R1 exact golden ledger parity is required before new outcomes. Flat-entry slope gates use only current/past SMA60. Next-open sizing uses signal-bar equity/close/ATR. No trade selection from future MFE, return or realized holding time.

Matching: same UTCmonth, HK6h block, causal prior252-bar ATR%quintile and side, deterministic SHA256 up to3 eligible non-signal controls, +/-12bar exclusion. Gate-admissible controls only. Report missing support. Controls have independent event state and are not a capital portfolio. For BE comparison, use the exact same all-eligible original-signal cohort and control IDs for both policies; no copied realized horizons. Event observations overlap and are not independent trades. Cluster sign flips by signal month,10000 draws, fixed seed20260907. Report unadjusted descriptive p-values, not new production significance.

## Required reporting and completion

MD+immediateHTML, Pine research candidate, code/config/receipt hashes, sequential decisions, all ledgers, yearly returns, concentration, exposure, AUC/top-decile/permutation diagnostics, same-cost SMA comparator, matched controls, accounting verification, explicit limitations. Final status remains inconclusive unless genuinely adequate validation exists; positive profit or a research screen is not project acceptance. No promise of profit. Native Pine compiler/TradingView ledger parity is a separate limitation if unavailable.

Builder and plan committed before execution; main only; preserve unrelated work. Large raw outputs remain local with registered hashes. Record learning notes. User asked for autonomous exploration, not perpetual blind parameter search.
