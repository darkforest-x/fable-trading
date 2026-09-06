# V8：冻结入场，替换管理规格

## Decision before new outcomes

Owner asks to keep improving 1h impulse/engulf trend entries and small-cycle
trend exits toward reliable post-cost profit. V7 already rejected:286requests,
net-17.7878bp, matched excess+5.0424bp with p=.1514. All V7 cases initially
align with native5m colour; weak release selection, not initial 5m opposition,
remains a possible cause. Another hypothesis is that5m management is too noisy.

This preregistration runs exactly two specs: native5mSMA40 versus native15mSMA40,
both genuine aligned-to-opposite edge, confirmations1. This is ONE management
SPECIFICATION replacement: aggregation, colour observation clock and memory
(3h20m versus10h) change together. Do not call it isolated exit-frequency effect.
No extra MA types/lengths, fee/stop/TP changes, filters or outcome-based retries.

## Fixed sample and two clocks

- Byte-pin V7's286case requests,849assigned controls,959source zones, all allocation
  and old outcomes before new price access. Three unmatched cases stay in286.
- Keep initial actualK1 extreme stop, nextreal5mopen, entryprice/risk/ATR, direction,
  source times and folds unchanged. No regenerate source events or rematch controls.
- Raw5m risk loop checks hardstop/gapstop before colour exit at the same instant;
  original72h request horizon,20bp roundtrip cost, missing-source censorship remain.
- Management updates only on its own completed native clock. Absence of15m close
  at+5/+10 is not missing data and must not reset the edge. Initial state uses
  floor(entry,M) latest completed bar; validate all completed underlying5m bars
  through entry, using only entryopen on the current rawbar. Unknown native state
  resets and waits for a valid aligned observation before an opposite edge.
- Keep original ltf_entry_* and known_5m_colour semantics. Independent mg_entry_*
  diagnostics separately record native5/15 state. Show cases AND controls' complete
  3x3 state crosstabs; no retrospective state-based selection.
- Recompute singlepending/position occupancy from the SAME959 arm times and new
  exits. Skipped known intention=0; selected unknown remainsunknown, not zero.

## Preregistered contrasts and gates

1. D_i=case15_i-case5_i on all286 original requests, never completed-only pairing.
2. I_i=D_i-mean(control15_ij-control5_ij)=excess15_i-excess5_i on the SAME283complete
   triplets. Keep3unmatched I unknown in ledger; they still contribute to D.
3. Serial intention delta on all959 source zones, selection-specific zeros as above.

Month-block bootstrap95% and one-sided monthly signflip, seed20260906/draws9999,
retain each case+its3controls+botharms together; not independent849control draws.
Require BOTH D and I mean>0, lower95>0,p<.01 (joint required; no pick-one rescue).
Also preserve V7 netmean>0,PF>1.1,all4foldspositive,>=80closed/>=12perfold,
>=12months/>=3eachfold,matchedcompletecoverage>=.9,matchedexcess>0,serialnet>0,
extra10bpnet>0,leavebest2net>0. Newnet andnewexcess also require lower95>0,p<.01.
Require complete case/control/zone evidence and paired286/283/959 finite support.
This is reused2023--2024 exploratory inference; even a pass is NOT independent
confirmation, profitability guarantee or live approval. No2025audit/holdout access.

## Ordering and evidence

1. Commit builders/config/plan/synthetic tests first; committed_sources verifiesbytes.
2. Validate V7 hashes/counts; Study development materializes no price after2024end.
3. Freeze both mg context arms before outcome replay.
4. Replay5m with extendedL3. Every saved old trade/episode/pair/serialcolumn must
   agree (timeexact including1ns,float1e-12,CSVempty/null equivalence only), not
   merely equal mean. Failure stops before15m; preserve attempt, fix contract.
5. Run15m, check allcase/controlentry invariants, compute complete paired ledgers,
   state/exit/loser diagnostics and serial intentions. Report all negatives.
6. DurableMD+HTML, tests, source/hash receipts, limitations and learning note.
   This turn makes no TradingView/production/training/promote/live mutation.

## Reproduction

Use existing contracted .venv; no dependency installation.

```bash
git branch --show-current
.venv/bin/python -m pytest tests/test_hourly_impulse_transition_15m.py tests/test_hourly_impulse_management_context.py tests/test_hourly_impulse_management_research.py
.venv/bin/python -m yoyo.evaluation.hourly_impulse_management_research
```

The runner intentionally refuses an existing results directory. Do not delete
previous attempts to rerun; preserve them and register a new attempt explicitly.
