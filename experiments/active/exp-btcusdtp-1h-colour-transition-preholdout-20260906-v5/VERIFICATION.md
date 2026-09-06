# V5 saved-ledger verification and reporting notes

Written after the run; not a preregistration or independent price replay.
Builder commit `af9d4d823dc56b696cf584f3cc557d64fc48a9ac` at03:26:59 UTC precedes
started03:27:00.054557 by1.054557s. All11 source/config/plan hashes equal both
saved receipts and builder git bytes. All12 pinned V4 inputs verified.

## Scope and mechanics

Driver receipt:219551 price rows, latest2024-12-31T23:55Z; physical archive341567.
No2025+ price materialisation, auditfalse, holdoutprice rows0. The independent
review verified the saved receipt, not a second full raw archive hash scan.

All857 old-policy requests reproduced all prior columns: directcase251x50,
directcontrol462x59, K2case55x63, K2control89x72. Two modes together have1714
requests and1672 completed trades;42 are the same21 K2 invalid-risk control
requests counted in both modes. All original mother episodes observed;0 censored
or unknown episodes. Every completed trade satisfies gross-net=.002, original
return formula, immutable initialstop and mother+72h bound.

Entry context exact open=entry-5min, availability=entry. Every actual transition
exit has previous_open+5m=current_open and current_open+5m=availability=exit_time.
No management reset or unknown context occurred on executed paths. Initially
aligned no-reset parity counts are247/453/22/13 for directcase/control then
K2case/control. Never-armed executed hard stops are respectively1/1/9/12;
these are charged real simulated trades, not zero non-entries.

## Findings independently recomputed

Direct251:247 initiallyaligned,4 opposite. Exactly4 changed,1improved3worse;
netmean-14.0213575 to-14.3066319bp; delta-0.28527435bp/mother.
All62 previous winners unchanged.37 opposite requests across direct4+K2 33
must not be described as37 changed results.

K2:22 aligned+33 opposite.27 old5min exits allinitiallyopposite and exactly
the27 changed paths;12 improve/15worse. Other6 opposite paths first realign on
the initialpostentry5minbar, so both rules later exit at the same true reversal.
28 unchangedexecutions plus196 nonentries explain224 unchangedmotherresults.
Old4winners:1improved3unchanged. Old51losers:11improved15worse25unchanged;
4turnpositive. No previous winner turns nonpositive.

The27 changed paths'12 gains sum+246.096event-bp and15 deteriorations sum
-220.173event-bp, leaving+25.92281event-bp, or+0.47132384bp/executedtrade,
or+0.10327813bp/originalmother. These are unweighted event sums, not account P/L.
By newexit kind,18 colour outcomes contribute+181.46734event-bp versus9 newly
hard-stopped outcomes -155.54453event-bp. The9 neverarmed outcomes averaged
-25.196 to-42.479bp, medianholding5to35min. The24 initiallyopposite paths that
later arm average-25.245to-17.684bp: stillnegative and FUTURE-DEFINED, not an
entry filter or a ready flat-wait strategy. NewK2 losers41/47 recordMFE<1R,
medianMFE .360R.6/47 haveMFE>=1R, versusold4/51; prioritytaxonomy assigning
them tohardstop/feeflip does not mean giveback vanished.

Four-arm fullmother matching154/251=61.35458%, frozen462controlmoments.
Means case/control/excessbp: directold-11.304012/-21.193879/+9.889867;
directnew-11.814822/-20.961355/+9.146532; K2old-2.967808/-2.767782/-.200026;
K2new-2.383540/-1.070430/-1.313109. Knowncontrolnontradezeros retained.
Serial replay independently agrees:direct251accepted/251trades each;
K2 250accepted/55trades each. No extra concurrency or free pending requests.

## Statistical assumptions and limitations

The statistical-analysis bundled assumption_checks import failed because
seaborn is not installed. No dependencies were installed. Existing SciPy's
Shapiro check used paired mother differences:directW=.09350046,p=7.3715e-33;
K2W=.29256399,p=6.2212e-30. Normality is not plausible, unsurprising with247/251
and224/251 exactzero differences. No IID t-test was substituted and no outlier
deleted. Planned monthblock resampling, all zero differences and24months remain.
These tests are descriptive, not a switch to shop for significance.

Diagnostic frequency counts over differencebp bins
[-1000,-50,-20,-5,-.000001,.000001,5,20,50,1000]:
direct[0,1,2,0,247,0,1,0,0], K2[0,4,9,2,224,2,6,3,1].
Raw graphical normality QA was not generated after the optional plotting helper
failed. Exact counts and SD/IQR are supplied, without claiming visual QA.

Reproduction of the fallback (savedresults only):
```bash
.venv/bin/python - <<'PY'
from pathlib import Path
import pandas as pd
from scipy.stats import shapiro
p=Path('experiments/active/exp-btcusdtp-1h-colour-transition-preholdout-20260906-v5/results')
for cohort in ['direct_k1_stop','wait_k2_k1_stop']:
    x=pd.read_csv(p/(cohort+'_paired_changes.csv.gz'))['difference']*1e4
    print(cohort, shapiro(x))
PY
```

## Report plan and QA

Single portableHTML, same technicalaudience and preserved V1-V4 report. Required
structure maps to existing summary, earlymetricdefinitions, priorvisualevidence,
methods, V5 state/paired/foldtables, uncertainty, risks andnextquestions. Only
title/summary/nextstep/count updates are dependentchanges; oldsection content,
sources andoriginal8-exit chartremain. NewV5sections use canonical v5_summary;
this verification file is linked supporting provenance, not a pre-run source.

New state and performance evidence is tabular because exactstate membership,
two distinct denominators, counts, elapsedtime and same-mother contrasts must be
auditable together; no single dominant scalar or rich temporal curve is being
claimed. Fourhalfyears are exactfoldchecks, not a four-point trendline. Existing
fullwidth eight-policy nativebar chart retains previous chartcontract andsource.
Build-report/visualize-data guide onefullportableHTML, not a second customruntime.
Standard packaging must still validate the exact payload; browser/mobile QA
remains unverified if the bundled verifier returns structural_only. No new
browser install or repeated unchanged Chrome workaround is justified.

Final regression:443 tests pass (104 V5new:40context+57execution+7driver).
No Pine/TradingView, activebundle, frozenpreset, VPS, forward, training,
promotion, deployment or real orders changed. Goal remains active and unproven.
