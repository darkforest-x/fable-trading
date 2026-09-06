# V18 — Confirm only the unprofitable native5 full exit

## Frozen question and one change

The owner's goal is a profitable hourly impulse system, not a promised return.
V17 reduced many losses but cut recovering trends; its full251 net remained
negative and worsened V16. Test fast_failed_launch_confirmations=2 against
the exact V17 candidate. V16 is a saved descriptive reference, not a third
search arm. V1 slow-state confirmation, V9 observation sampling and V12 frozen
hourly MA exits already exist; none has this exact before-partial mechanism.
The count2 is a fixed exploratory hypothesis, not a claimed optimum.

First true completed native5 aligned-to-opposite edge, latest completed
native15 still aligned, before any partial: actual raw5 OPEN gross>20bp
keeps the existing immediate50% original-notional partial. Gross<=20bp
creates pending, no fill. Only the immediately next consecutive completed
native5 bar, same valid raw/management source segments and still opposite,
may confirm. Recheck latest completed slow15 and gross<=20bp at its actual
raw5 OPEN; execute there, never at the first trigger price.

If any confirmation condition fails, cancel once and consume that edge.
Do not wait for later profit/colour; do not invent a half on a confirmation
bar with no real new edge. New pending requires later alignment then a new
true reversal. Multiple pending cycles per trade are allowed. Confirmation
is opposite-to-opposite, not a second flip: separate logs and counters.
failed_launch trigger fields retain the first true edge evidence; actual
confirmation fill is separately recorded in failed_confirm and exit fields.

## Fixed entry, source, clocks and accounting

Original1h K1 body strictly crosses SMA40(HL2); own MA-side must match direction,
close location>=.70. Large body>=.65 and range>=1ATR, OR true body engulfing
and range>=.65ATR. Next raw5 open entry, original K1 extreme stop, max72h,
20bp roundtrip unchanged. No K2, no entry gate, no stop movement, no new MA,
no time-limited activation, no fraction change or slow-remainder change.
Native5/native15 SMA use only current completed bar plus39 contiguous bars.

Priority remains source/invalidopen, gap K1 stop, slow full exit,72h deadline,
pending confirmation or true fast edge at open, then later HLC/intrabar stop.
If waiting bar hits stop, engine finishes at that bar's end T+5 BEFORE the
next loop could confirm. Known changed exits must be>=old full T+5; censored
unknowns may use T, never imputed to zero. Invalid HLC after a valid open
cannot cancel an already executed open decision. Same-open information/fill
assumes no extra execution delay; disclose this research idealization.
Decimal quote economics preserves strict>20bp partial/equality full. Weighted
costs sum to one20bp roundtrip.30bp stress changes cost, not fills/threshold.

Prices: only prefix before2025-01-01, including pre2023 indicator warmup;
trading folds reused2023--2024 with original72h boundary embargo. No2025+
prices/audit/holdout. Holdout consumed0. Physical archive timestamps may be
checked without loading later price columns. No downloads or source switch.

## Pre-outcome freeze and audits

1. Commit engine, runner, config, this plan, synthetic tests before raw replay.
2. Pin original251 cases/462 own controls/assignments and entry contexts;
   regenerate identities, freeze713 fast and1426 slow initial context rows
   before runner reads/hashes old outcome anchors. Never subset152 old fulls.
3. OFF exactly reproduces V17 candidate six ledgers, every old field; preserve
   opaque source IDs at CSV read. Candidate retains all original entry/risk.
   V17 nonfull paths all old fields identical. First pending equals old full
   time. New confirmed full IDs are a subset of old full IDs. No shared serial
   mask assumption: recompute each arm, keep all251 portfolio opportunities.
4. Export full ledgers, real fast edges and separate pending events; diagnose
   restored recoveries, new deeper losses, partial restorations, stops and
   unknowns. Future MFE/old wins label failures only, never trade selection.
5. Compare all251 paired D and154 matched I with97 unmatched unknown; same
   seed20260906 and9999 calendar-month bootstrap/sign flips. No random time
   split, outlier deletion, posthoc observed power or test shopping. Show SD,
   median, histogram, quantiles/missingness and assumption limits before
   inference interpretation. Reused observational months are not randomized
   market assignment or independent validation; raw p is exploratory across
   the V1--V18 research family. No model AUC; range/ATR AUC/top-decile remains
   descriptive single-feature baseline. Both studies require positive D/I.
6. Independent saved-ledger verification uses no raw strategy imports;
   verifies arithmetic, original IDs, contexts and logged confirmation clocks.
   It does not independently prove raw SMA or every absent/unlogged event.

## Unchanged acceptance and delivery

Net>0,PF>1.1,all4 halvespositive,>=80events,>=12/half,>=12active months,
>=3months/half,positive single-position/cost30bp/leave-top-two results,
>=90%matched coverage and positive matched excess. D/I each need positive
mean,95%lower>0 and month p<.01. Known154/251=61.35% coverage cannot pass90%;
do not drop97 or lower the gate. No candidate promotion on reused data.

Run .venv/bin/python -m yoyo.evaluation.hourly_impulse_failed_confirm_research.
Refuse existing results, preserve failed attempts before any corrected retry.
Existing Python3.9.6,pandas2.3.3,numpy2.0.2,scipy1.13.1 unchanged. No installs,
training, ACTIVE, TV writes or live orders. Deliver source MD and immediately
convert to analysis/html/p1_btcusdtp_hourly_failed_confirm_v18_20260906.html.
Saved source-backed technical report, failure tables and honest QA limitations.
If rejected, retain negative evidence and propose the next bounded action;
the profitability goal remains active until genuinely achieved.
