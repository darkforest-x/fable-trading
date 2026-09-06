# V4 saved-ledger verification

Completed after the single development run. This is a manual audit record,
not a preregistration or a second independent market-data replay. All numbers
below were recomputed from saved case/control/request/episode CSVs. No new
price source or parameter selection was introduced.

## Lineage and contracts

- Builder/config commit a169fa6 at2026-09-06 02:50:23Z precedes started.json
  at02:50:30Z. All11 recorded source hashes match that commit and current files.
- Materialised archive prefix219551rows ends2024-12-31 23:55Z. Holdout0.
- Original251 motherIDs retained in every arm; same462 controlmotherIDs.
- A matches V1 development_exit_5m_native40_trades.csv.gz on251IDs, clocks,
  prices, stop, ATR and gross/net returns.
- B/C same55 case requests and89controlrequests. Their request frames differ
  ONLY in initial_stop. K2 geometry independently recalculated55/55 valid.
- Case waiting gaps1..8 counts16,13,6,2,6,5,4,3. All integer actual UTC hours.
- All980 completed case/control records respect maternal+72h and20bp cost.
- All case/control episodes observed; no gap or unresolved mark countedzero.

## Matching and serial accounting

154 original mothers have3controls each,462 distinct controltimes; coverage
61.3546%.94mothers lack enough exact controls,3lack causal matching support.
No widening or dropping those97 from the strategy denominator.

B has89controlrequests but68executed:21have nonpositive risk to their virtual
K1 stop at K2 nextopen, recorded entry_invalid_risk with observedzero.
C uses the SAME89requests; changing only stop makes those21valid. Their
mean net is-15.66bp,2wins. B/C include394/373 non-entryzero controls. Complete
three-control means produce excess A+9.8899/B-0.2000/C+0.5970bp per mother.

Serial pending accepts251A mothers; B/C accept250 andblock1. That blocked
mother's independent waiting path itself expireszero, leaving all55actual
trades intact. Selection begins at motherdecision, not at eventual K2 entry.

## Timing and missed-opportunity decomposition

The future-defined55mothers that eventually yield K2 have A direct mean
-43.1501bp(7wins48losses), versus B waiting-22.9202bp(4wins51losses).
Same55 improvement is+20.2299bp:38improve,17worsen. This group is NOT an
entry-time classifier. The skipped196mothers have direct mean-5.8475bp,
including55wins141losses. Total intention improvement+8.9990bp consists of
+4.5662bp participation reduction and+4.4328bp timing on entered mothers.

B/C each have27/55five-minute exits,43/55within30minutes; overall median
holding10minutes, losing-trade median5minutes. B's27areallcolour_exit;
C has25colour_exit+2hard_stop. B47/51 and C45/51losers neverreach1R whileheld;
median losing MFE0.1871R/0.2353R. Excursions do not cover post-exit paths.
K2 ownhourlyHL2opposite colour is3/55, only1ofthose exitsin5minutes. Thus
most quick exits are not explained by that source-valid K2 exception.
Savedledgers establish opposite colour AFTERentry, not a pre-entry colour
state or an actual aligned-to-opposite transition. Those need a new causal
clock-state diagnostic; do not infer them from the exit label.

## Read-only reproduction example

```python
from pathlib import Path
import pandas as pd

p = Path('experiments/active/exp-btcusdtp-1h-first-k2-preholdout-20260906-v4/results')
a = pd.read_csv(p / 'direct_k1_stop_case_trades.csv.gz')
b = pd.read_csv(p / 'wait_k2_k1_stop_case_trades.csv.gz')
c = pd.read_csv(p / 'wait_k2_k2_stop_case_trades.csv.gz')
same = a.loc[a.event_id.isin(b.event_id)]
assert len(a) == 251 and len(same) == len(b) == len(c) == 55
print(same.net_return.mean()*10000, b.net_return.mean()*10000)
for frame in (b, c):
    print(frame.hold_minutes.eq(5).sum(), frame.hold_minutes.le(30).sum())
    print(frame.loc[frame.net_return.lt(0), 'hold_minutes'].median())
    print((frame.net_return.lt(0) & frame.max_favourable_r.lt(1)).sum())
```

## Report design and limitations

Technical analytical audience, portable HTML only. The existing full report's
eight-policy signed bar chart is retained. V4 uses exact tables because the
key comparison needs BOTH per-trade and per-mother denominators plus matching
coverage and phase counts; a single mean bar would conceal that distinction.
No claim of independent confirmation, powered significance or liveprofit.
All previous report sections remain; only dependent summary/next-step text
changes and new V4 sections are added. Standard HTML packaging performs schema
and payload checks; absent compatible browser QA remains disclosed.
