# SPIKE native V1: exact three-rule exit study

Owner explicitly requested this combined experiment on 2026-09-14. The supplied
0.65/0.5/2/50% exit thresholds and RV20/risk30%/asset filters are fixed before
outcome replay; this is the authorized exception to single-variable batching.
No Pine, live service, notification, account, model or original replay edit.

## Two distinct denominators

1. `original_ledger`: reconstruct every available event in the immutable native
   V1 ledger `b15b69b8864e5eb651f2417fc6681ea688b5fbb95dacd15af1284b42744e3578`.
   Its 6253 rows include censored events, 30m/1h/4h/daily and three venues over a
   different two-year window. Original raw/cache receipts must match. This
   track checks the quoted XLSX +4326R approximation on the same denominator;
   unlinked/unreproduced events must be explicit, never silently discarded.
2. `binance_top20_202308`: discover historical Binance USD-M USDT perpetual
   monthly archive directories; rank by actual August 2023 quote-asset volume,
   not current rank or subsequent returns, and freeze 20 assets. Preserve
   subsequently delisted assets and August new listings with available history.
   Analyze 15m/1h/4h from 2023-09-01 inclusive to 2026-09-01 exclusive UTC.
   Prefer July-August warmup, >=341 bars per continuous feature segment. Missing
   input/metadata and coverage gaps are disclosed. Rankings and downloads use
   data.binance.vision ZIP plus CHECKSUM; existing authenticated ZIPs may be reused.

Top20 is a fixed pre-period cohort, not a promise to cover every subsequent hot
listing. RAVE may be absent; in that case ex-RAVE top20 is identical, and the
original-ledger ex-RAVE sensitivity answers the concentration question.

## Frozen strategy and execution

- Signal engine: unchanged `spike_burst_replay.features()` and `replay()`, frozen
  Pine source SHA 18bbb6955fdf12e124688003799c44fc2a641f11a342edcf478157b1c9641fe2.
  This frozen V1 is LONG ONLY. Do not introduce short signals or use V8/common-V1.
- Native initial reference and trailing path use signal-close entry/risk; fills
  use the next ordinary bar's open. New protection triggers and reported R use
  actual entry minus the frozen initial stop. Initial R never changes afterwards.
- Original 0.002 nominal round-trip cost stays fixed, including losing exits and
  entry-price BE. Funding and realistic account leverage/slippage are unmodelled.
- Each bar first checks the previously operative stop (gap uses available open).
  Only a surviving completed bar may update observed MFE/MAE or next-bar stops.
  Same-bar high/low ordering is not invented. Stop-touched bar excursions are not
  used to upgrade the protection before that stop.
- MAE = adverse excursion FROM ENTRY, not retracement from the running peak.
  After observed adverse >=0.65 original R, next-bar stop is at least entry-0.65R.
  If the next open already lies below this level, fill at that open, not at0.65R.
- MFE >=0.5R raises next-bar protection to actual entry. This is price BE, not
  fee-adjusted net BE.
- Observed cumulative MFE >2R sets next-bar protection to entry+0.5*MFE*R.
  Ratchet the tighter of native/adverse/BE/lock; never loosen. No fixed TP.
- Supplied 2.20R phase slice and RV<6/TR<4/risk5%-30% sweet zone are source
  hypotheses only, not extra admission rules or reasons to retune this trial.

## Fixed arms and entry bundle

`baseline`, `adverse65`, `be05`, `lock50`, `triple`, `filters_only`,
`filtered_triple`. Main comparison reports baseline/triple/filtered_triple and
ex-RAVE triple; single arms and filters-only isolate attribution. Do not sum
individual deltas into a claimed combination result.

Bundle rejects final native signal RV>20, next-open initial risk/entry>0.30,
USDC, PAXG, and confirmed US-listed stock-linked assets. PAXG is gold-backed,
not a USD stablecoin. Crypto names matching stock tickers are NOT enough.
Use venue asset-type metadata first and a frozen primary US-listing directory
for geography; unresolved stock identities are retained/flagged, not silently
treated as verified US stocks. No outcome-ranked exclusion beyond requested RAVE
diagnostic. No source quote's descriptive path cluster can read future bars.

## Deduplication and controls

- Keep both raw-event and deduplicated results. Primary new-pool table keeps the
  earliest executable entry per underlying asset x UTC entry day across venues
  and timeframes. Ties:4h,1h,15m (source adds1d before4h and30m before15m), then
  Binance,OKX,Gate, then lexical event ID. Only time/identity/open-time-valid risk
  may choose; never best eventual R. Dedup happens BEFORE the entry bundle,
  with no replacement if that first candidate is later filtered out.
- Same-asset same-UTC-day random entries use complete causal features, the same
  timeframe/direction, same initial-stop formula and same exit arm/cost. Match
  on fixed causal ATR/price bins [0,.005,.01,.02,.05,.1,infinity]. Do not compute
  volatility ranks from a future trading window. Up to20 deterministic hashed
  draws per event without replacement; no cross-day/asset fallback. Report
  unmatched and partially matched events. Random controls do not inherit the
  original event's future holding duration or exit time.
- For filtered arms, controls must satisfy the same admission bundle. Compare
  only matched, jointly observable completed outcomes for excess, alongside
  full event tables and censoring. Random draws are hypothetical matched
  opportunities, not twenty simultaneous funded trades.
- Cluster bootstrap by asset x UTC day,5000 draws, fixed seed20260914, for mean
  netR and matched excess95%CI. One-sided cluster sign/permutation test5000
  draws for positive excess; adjust 3 main arms x3 timeframes=9 primary tests.
  No repeated resampling until a threshold passes.

## Time separation, measures and honesty

Dev [2023-09-01,2025-09-01); test [2025-09-01,2026-09-01). Report test months
in chronological order with all policy parameters frozen. Separately report
test before2026-05-04 and repository holdout-era [2026-05-04,2026-09-01).
Prior exposure and outcome-conditioned XLSX hypotheses prevent claiming blind
OOS. Each new configuration's first full run and every failed/sample replay
are recorded; later data are not used to tune this experiment.

Report trades, net winrate, mean/sum netR, nominal returns/cost decomposition,
top-five positive-trade contribution and total without them, realized10R count,
mean matched excess+CI/p, closed-event cumulative-R drawdown, and censored count.
Event drawdown is not account maxDD or an intrabar mark-to-market account curve.
No account percentage returns are claimed without capital/margin assumptions.
The source XLSX numbers are supplied hypotheses, not proven mathematical upper
bounds; exact replay can differ through timing, ratchets, gap fills and overlap.

## Delivery and ownership

Root owns this protocol, registry, coordinator, statistics and final report.
Terra engine owns new triple-exit module/tests and engine artifacts; Terra data
owns new archive/ranking wrapper and data artifacts. No nested delegation.
Commit builders before materializing outcomes, validate frozen baseline parity,
keep source receipt hashes and failed outputs. HTML report is mandatory; local
CSV ledgers and a comparison workbook accompany it; valuable findings go to
Spike Notion as research, without changing live eligibility.
