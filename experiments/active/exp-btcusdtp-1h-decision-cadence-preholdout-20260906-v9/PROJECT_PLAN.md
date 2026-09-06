# V9: fixed 5m features, sampled 15m exit decisions

## Question and scope, frozen before outcomes

V8 did not establish profit: native15m SMA40 changed both memory and observation
frequency; mean net was -19.9065bp versus -17.7878bp for native5m. Test exactly
one further contrast: retain the entire native5m SMA40(HL2) stream but sample
exit decisions at UTC quarter hours. No additional sweep or outcome-driven retry.

This is a bounded mechanism experiment on V7's source-zone breakout family.
That entry family does NOT require the owner's original 1h MA-cross condition.
It must not replace the stated hourly impulse/engulf-cross strategy by stealth;
success here would motivate separate frozen validation on that original family,
not demonstrate that the complete requested system is profitable.

## Fixed population, economics and clocks

- Pin the same 12 V7 input hashes, including 286 case requests, 849 controls,
  959 source intentions, allocations and baseline outcomes. Three unmatched
  cases remain in every case denominator. Do not rematch or regenerate entries.
- Preserve actual entry open/time/direction, K1 extreme stop, signal ATR/risk,
  72h horizon, 20bp roundtrip cost, source zones and four 2023--2024 half-year folds.
- Both arms use exactly native5m SMA40 of HL2=(high+low)/2. Colour is +1 when
  completed HL2>=MA, -1 below; unknown remains unknown. No synthetic 15m OHLC,
  SMA120, smoothing, slope threshold, filter or new feature.
- Baseline: original native5m adjacent completed colour edge. Candidate:
  `management_minutes=5, decision_minutes=15, transition_colour, confirmations=1`.
- Seed at actual entry E using the latest 5m bar ending exactly at E. Never exit
  on this seed. The first eligible check is floor15(E)+15min: wait15/10/5min for
  entry phases0/+5/+10. At check T use only the completed native5m bar [T-5,T).
  First seed-to-check distance may be15/10/5; subsequent sampled observations
  must be15min apart. Exit at real raw5 open T if sampled previous aligned and
  sampled current opposite. Initially opposite/unknown must first sample aligned.
- Valid intervening colours do not update/arm/latch the sampled state. A full
  transient flip and recovery between checks does not exit. This is a sampled
  state transition, not an adjacent native5m edge delayed to the next check.
- Raw5 hard-stop/gap-stop protection and data validation remain active every5m.
  At each complete5m timestamp validate management continuity/finite state;
  off-clock missing/invalid/segment change clears sampled state but never arms.
  A valid scheduled observation may seed a fresh sequence. Raw data gaps remain
  censored, never imaginary fills. On a common timestamp resting gap stop wins.
- Preserve old ltf_entry_* and append identical mg_entry_* context to both arms.
  Compare every entry/context field before outcomes; context cannot select cases.
- Recompute single-position occupancy on all959 original source intentions.
  Skipped known intentions are zero, selected unknowns remain unknown.

## Prespecified analysis and failure diagnostics

1. D=case15check-case5check on all286 requests.
2. I=D-mean(control15check-control5check) on the same283 complete triplets;
   all286 rows remain in I ledger, three unknown rather than fabricated zeros.
3. Serial intention delta on the original959 zones, not selected-trade-only means.

Reuse monthly block bootstrap95% and one-sided monthly signflip, seed20260906,
9999 draws; keep each case, its three controls and both arms together. These
development years have already been reused repeatedly: p values are exploratory,
not familywise-confirmatory evidence. No IID treatment of overlapping events.
No outlier removal, completed-only pairing, future-MFE filter or winner subset.

Joint D and I must each have positive mean, lower95>0 and p<.01. Also require
new net and matched excess with positive mean/lower95 and p<.01; mean net>0,
PF>1.1, all4 folds positive, >=80 closed, >=12/fold, >=12 active months and
>=3/fold, matched coverage>=90%, positive serial net, positive extra10bp stress
and leave-best-two net. Complete evidence and finite286/283/959 pairing required.
Any failure rejects this candidate without opening audit data.

Report paired distribution, old-win/new-loss versus old-loss/new-win, hard-stop
and colour-exit transitions, holding-time change, all loss categories, MFE-based
giveback descriptions (outcomes only), matching controls and fold comparisons.
Show independent request outcomes and serial intention accounting separately.

## Ordering, sources and delivery

1. Commit code/config/plan/synthetic tests before real-price access; enforce
   committed_sources and all parent byte hashes. Existing contracted runtime:
   pandas2.3.3, NumPy2.0.2; no dependency installation.
2. Use Study(development), only2023--2024 prices. No2025+ or holdout read.
3. Freeze identical original5m contexts for both arms before outcomes.
4. Baseline must match every V7 saved trade/episode/pair/serial column (exact
   timestamps, numeric1e-12, CSV null normalization only). Stop on disagreement.
5. Evaluate candidate once; verify entry invariants, controls and complete ledgers.
6. Independent code/synthetic and saved-ledger review, MD+HTML report, provenance,
   test receipts and learning note. Do not erase previous results to rerun.

Pandas clock semantics: https://pandas.pydata.org/pandas-docs/version/2.3/reference/api/pandas.Timestamp.floor.html
Confirmed-bar source: https://www.tradingview.com/pine-script-docs/language/execution-model/
These references establish API/timing semantics, not profitability.

No TradingView overwrite, training, promotion, VPS/forward/deployment or live
orders. Even development success requires a frozen, genuinely fresh independent
evaluation; reused2025 audit data cannot provide it. Owner profit goal stays active.

## Reproduction

```bash
git branch --show-current
.venv/bin/python -m pytest tests/test_hourly_impulse_transition_cadence.py tests/test_hourly_impulse_cadence_research.py
.venv/bin/python -m yoyo.evaluation.hourly_impulse_cadence_research
```
