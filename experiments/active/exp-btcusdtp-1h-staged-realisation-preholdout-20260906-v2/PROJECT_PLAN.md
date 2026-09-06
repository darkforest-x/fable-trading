# Staged realisation after hourly impulse

1. Keep V1 SMA40 hourly entry with same-direction three-bar MA slope. No entry
   threshold search. Same 2023/2024 halfyear folds, 72h entry embargo and 20bp costs.
2. Compare six frozen management policies on exactly the same entries. Actual
   partial realisation is not simulated by merely moving a stop to breakeven.
3. Protective SL always wins ambiguous same-5m SL/target ties. At an open,
   SL-gap precedes existing partial limits, then confirmed colour exit. A takeover
   reached within the previous 5m bar becomes eligible only after the old-clock
   exit decision at the next open. No retroactive cancellation of exits.
   At activation, exit immediately if the latest already-completed hourly bar
   beginning at/after entry is opposite; do not wait one more hour against known
   colour merely because activation occurred between hourly boundaries.
4. Independently test zero-target baseline parity, long/short symmetry, multi-target
   quantities, gap fills, partial censoring, confirmation clocks and 20bp accounting.
5. Commit builder/config before outcomes. Record all six results and baseline/final
   random controls with the V1 same-month/session/volatility/colour matching contract.
6. Open no new transport data unless dev count, all-four-fold profitability, PF
   and exact matched-control gates pass. Otherwise reject locally and preserve the
   original negative V1 audit without repeatedly tuning it.
7. Deliver reproducible Chinese diagnosis and HTML. No Pine, deployment or live state
   changes, no new models, no repository holdout prices.
