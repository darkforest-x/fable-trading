# ETH 4h trend candidate — owner asks for a profitable strategy

Owner request: “给我做一个赚钱的策略”, 2026-09-07. Research implementation is authorized; no live orders, deployment, holdout, or profitability guarantee.

## Frozen objective before new outcomes

Starting from ALLIN V7, produce runnable Pine with honest execution and test whether the complete candidate is profitable after nominal 0.2% round-trip commission. Desired maximum modeled drawdown <=20%, per-trade planned price-stop risk 0.5% equity, notional capped at1x. These are research targets; gaps, fees and funding can exceed a planned risk budget. The optional owner drawdown preference is pending; no reply means the announced20% target applies.

ETHUSDT perpetual Binance USD-M, ordinary UTC4h candles, original 10/60 SMA, EMA100 and oscillator entry. Original ATR14x4, hard-stop3%, BE trigger1.5%/offset0.1%, volatility0.1-10%, Sunday UTC filter, skip thresholds2%/20% with1/7 counts unchanged. No threshold optimization or new TP/SL values.

## Single-variable sequence

- S0: existing immediate-stop arm, only sizing schedule changed to fixed1x.
- S1 vs S0: only risk-based quantity, 0.5% planned risk,1x cap.
- S2 vs S1: only same-side stop replacement becomes monotonic tightening.
- S3 vs S2: only per-position stop state is isolated from an opposite entry's pending stop.
- S4 vs S3: only cooldown boolean is evaluated after incorporating newly closed trade P/L.
- S5 vs S4: only an opposite signal closes the position without opening a reverse position.

The final candidate is S5 regardless of which intermediate row has the highest return. No picking the best row. Failure of S5 remains failure; do not silently adopt an earlier row. Parent Replay extension hooks must keep original defaults byte-equivalent on frozen golden ledgers.

## Data and evaluation

Read only known-hash local Binance15m archive through2026-04-30, aggregate complete16-bar UTC4h groups. 2020 warmup. Separate development2021-2024, already-exposed replication2025-Apr2026, and continuous2021-Apr2026 accounting audit. All three windows are registered before execution; no truly unseen claim. Source hash07013f996a13a0b205f9df6df2af5deab5d601e7e34b42bfc308dc13aef400bd; holdout2026-05-04 never read.

Exact same frozen matching protocol as the first ETH4h replay: month, HK6h block, causal prior252-bar ATR% quintile, same side and policy, up to3 hashed non-signal controls, exclusion +/-12bars, no realized holding-time match, missing matches retained. Random events are not a portfolio. Independent controls have independent cooldown history. 10,000 month-cluster sign flips and abs(osc) diagnostic AUC/top-decile/permutation.

Each window starts flat at500USDT and administratively closes any remaining position at its last bar close, including exit commission. Controls use the same boundary rule. Pine uses the corresponding last-close administrative exit.

Economic research screen for S5: positive after-boundary-close return and positive paired mean excess in both separate windows, path drawdown<=20% across all windows, no nonpositive equity, ledger checks pass. Production remains ineligible even if the screen passes: small sample, reused history, no native parity, funding/slippage omitted. Report sample counts, concentration, yearly losses, realized/marked distinction and a same-cost SMA-only entry comparator. No funding from a different venue.

## Deliverables

Commit builder before outcomes. Pine research strategy (ETH4h/date guarded, no live credentials/alerts created), Python replay chain, tests for unchanged original defaults and each hook, ledgers/controls/equity, source hashes, machine-readable economic screen, risk-and-honesty report MD immediately converted to HTML and opened. Record learnings. Only main; preserve unrelated working changes.
