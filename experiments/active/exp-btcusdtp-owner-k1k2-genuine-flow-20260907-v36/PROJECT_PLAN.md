# V36 — Complete source-defined K1/K2, one genuine-flow hypothesis

Frozen before new V36 market materialization. Research only; no parameter search.

## Scope and lineage

- Use ONLY the 24 hash-attested V34 Binance USD-M BTCUSDT 5m price/flow files, 2023-01-01 inclusive through 2025-01-01 exclusive. No 2022 warmup or 2025+ prices. Same-venue OHLC and taker flow; this is NOT historical OKX/Pine performance parity.
- Complete 12-slot UTC hours, segment-reset SMA40(HL2), Wilder ATR14, source six-MA rope ranking. Exactly owner-causal V2 `f_findBestK1` geometry: gap2..8, K1 body .65/range .95ATR/location .70/cross depths -.05/HL2 aligned; K2 wick .25/body .50/touch0..1.50/closeback0/MA not in body; strict middle close and HL2 side. Highest original quality per K2/direction, shortest gap ties. All complete K2 candidates including last bar, no future-row test.
- This is a **pure morphology independent-event experiment**, not a reimplementation of the entire published Pine. Do not use its fee/risk preset, ATR admission bounds, 6h cooldown or consumed-K1 memory to prefilter the universe. Those production presets are NOT changed. Preserve full candidate identities, then separate a diagnostic single-position ledger for each arm.
- Existing V4 research exit is fixed: enter K2-close boundary at actual next 5m OPEN, stop K2 extreme, native5m SMA40(HL2) first completed post-entry opposite **state** (`colour`, not `transition_colour`), max72h, .002 round-trip assumed cost. No fixed TP/protection/partial or stop tightening. Gap-stop fills at actual open, missing paths unknown, never fill zero. Existing source engine unchanged.

## One hypothesis / no tuning

H1: directional genuine quote-flow imbalance over [K1 close, K2 close) is strictly positive. Compute sum(delta_quote)/sum(quote), not average bar ratios. Known all-zero, missing or unavailable windows fail closed and remain auditable. Equality does not pass. No K1 flow, second window, additional indicator, optimized threshold or future outcomes in the gate.

Controls receive the case gap duration but use their OWN price/ATR/clock/flow. Transferred gap is a design attribute, not a detected control K1. Each original matched case keeps all three controls, including controls whose own flow gate fails. Assignment before any price-path outcomes or gate selection, same-month/UTC6h/previous720h ATR tercile (min168)/known native5m colour/known hourly colour/directional slope sign/direction. No reuse, no relaxed fallback; report every unmatched case. Existing matching algorithm fixed, so matching is observational and retrospective within-month, not a live allocation rule.

## Time and accounting

Four fixed calendar half-years; use ALL available cases, no outcome stopping rule. Keep full geometry output; economic fold excludes decisions in final72h by CLOCK only. No trade straddles a fold. Reject invalid next-open risk transparently, distinguish rejected entry (known no fill) from censored path (unknown). Flow is theoretical close-boundary availability, not verified live delivery.

Primary matched estimand on original case units: (gated-case net − baseline-case net) − mean(gated-control net − baseline-control net), gate-off earns0 and costs0. Also show baseline and gated absolute matched excess; cash0 and whole-case net must be beaten, not just save more fees. Decompose incremental gross and saved-cost terms exactly. Unknown paths make the pair unresolved, never zero-fill or silently subset. Report matched coverage and all original-control denominators.

One-sided month-cluster sign-flip (9999,seed20260906), 95% month-cluster bootstrap; all 24 calendar months including empty months, intact same-month matched sets, resample within each halfyear. Month-boundary path overlap and reused development data limit inference; do not claim confirmatory significance. No power claim from observed effect. Fixed available24months is sample-size rationale. Primary alpha.01; descriptive secondary diagnostics do not choose a new policy.

Research progression gates inherited from V4: >=80 retained closed events, >=12/fold, four positive folds, PF>=1.1, >=12 active months, >=3months/fold, >=90% complete matched coverage; primary p<.01 and CI lower>0, retained mean net>0, absolute gated matched excess>0. Passing still requires independent validation/fresh forward data and does not authorize deployment.

## Failure analysis

Per-event export: geometry, flow amount/ratio/availability, gate, entry/exit/risk, net/gross, exit reason, cost-erased gain, peak excursion before exit, overlap rejection. Summaries: retained/removed winners and losers, stop/colour/timeout, gap2–4 vs5–8, direction, four halves. No posthoc threshold search. Top-decile/AUC of flow and K1-body single-feature baseline are descriptive on reused data, not ML validation.

## Verification / delivery

Commit builder, contract and tests before reading market columns. Synthetic prefix/unknown/zero/control identity/cost invariants; real quarterly candidate and flow prefix parity; exact full matched sets before labels; persist no-outcome candidates/gates/assignments before replay. Recheck hashes, no overwrite. MD then immediate HTML conversion, report includes all negative results, limitations, reproduction and next step. Keep eligibility false.

Audit caveat: a helper's overly broad source search accidentally displayed one OLD owner anchor row dated2026-09-03; it was not used. This is recorded separately; V36 runner's price-path inputs remain strictly2023–2024. Do not claim zero out-of-scope inspection for the entire investigation.
