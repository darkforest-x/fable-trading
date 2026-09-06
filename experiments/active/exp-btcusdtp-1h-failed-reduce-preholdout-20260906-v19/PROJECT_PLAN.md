# V19 — Confirmed losing half, slow remainder; final exit-only probe

## Resumption provenance (2026-09-07, before V19 price replay)

This prepared V19 was paused for the owner's ChartPrime source audit and the
V20--V22 independent entry-gate probes. Those probes are now rejected. Resume
the original fixed50% V19 without incorporating their outcome-conditioned
subgroups or changing its threshold. The ID preserves its preparation date;
actual run/commit timestamps in receipts establish execution chronology.
This adds no new independent sample or holdout authorization. Baseline and
candidate each recompute the251-case single-position ledger; the462 matched
random controls are independent outcome comparators, not an additional
tradable single-position portfolio. Do not claim four serial ledgers.

## Frozen question and one change

V18 confirmed fast failures still lost money on all four reused halves.
Test original-notional confirmed execution fraction1.0 versus0.5. Confirmation
count2, native5/native15 SMA40(HL2), entry cohort, stops, fees and clocks stay
unchanged. This fixed50% hypothesis is not a claimed optimum. It is risk
reduction, not TP, because the executed half is not profitable after costs.
No grid, extra exit threshold, additional filter or alternate price source.

At a true completed native5 aligned-to-opposite edge before any fast fill,
latest completed native15 must still be aligned. Actual next raw5 OPEN gross
above20bp takes the existing profitable half. Gross at or below20bp creates
pending; only the immediately consecutive valid native5 opposite bar can
confirm, with slow alignment and new OPEN economics rechecked. Failed checks
cancel the edge. Recovery above20bp at confirmation only cancels: without
a new true edge it cannot invent TP. Confirmed execution is exactly at the
V18 full-exit time and price, now for50% only. The untouched50% follows the
original slow15/K1 stop/72h path. Either kind of fast half consumes the one
shared fast-fill allowance. There is no later second half from another edge.

## Entry, source, causal priorities and accounting

Original1h K1 body strictly crosses SMA40(HL2), own side matches direction,
close location>=.70. Large body>=.65 and range>=1ATR OR real body engulfing
and range>=.65ATR. Next raw5 open entry; original K1 extreme hard stop;
max72h,20bp roundtrip. No K2 gate, stop movement, new MA or new entry gate.
Features use completed contiguous bars only (current plus39 for native MA).

Priority is source/invalidopen, gap stop, slow full exit,72h deadline, pending
confirmation or new fast edge at OPEN, then same bar HLC/intrabar stop.
A partial at OPEN must not skip that bar's stop or source-censor check.
Same-open observation/fill assumes no extra execution delay, not live parity.
Original-notional gross is .5*gross(reduction)+.5*gross(final). Costs weight
to one20bp roundtrip, not20bp per leg. Only new actually reduced paths use
Decimal quote economics on both legs; old non-reduced float results remain
unchanged.30bp stress alters costs only, never trigger thresholds or fills.
Known reduction cannot uncensor an unknown remainder: whole-trade gross/net/R
stays unknown, while realised-half cashflow is separately retained.

Prices are only the pre2025-01-01 prefix, including pre2023 warmup. Reused
trading folds2023--2024 retain the original72h boundary embargo. No2025+
price/audit/holdout read. Holdout consumed0. No download or source switch.

## Pre-outcome freeze, independent accounting and controls

1. Commit engine, runner, config, plan and synthetic tests BEFORE raw replay.
2. Pin251 original cases,462 own controls, assignments and entry contexts.
   Regenerate identities and freeze713 fast/1426 slow entry-context rows
   before reading/hashing old outcome anchors. Never subset old failures.
3. Baseline reproduces exact V18 candidate six ledgers, preserving opaque
   CSV IDs and every old field. Candidate retains entry/risk and all old
   non-full paths. Its risk-fill mask must equal old confirmed-full mask;
   its fill time/price must equal old full time/price, without extra delay.
4. Independently pin V16 candidate final-structure columns: event_id,
   exit_time,exit_price,outcome,closed,hold_minutes,max_favourable_r,
   max_adverse_r. V19 final path must equal that slow/stop/deadline path;
   do NOT borrow V16 PnL. Earlier risk half may replace a later profitable
   half that V16 took. Do not average the V16 and V18 whole-trade returns.
5. Recompute each arm's own single-position mask. Longer individual holdings
   can block one trade and permit another; masks are not monotonic subsets.
   Keep all251 portfolio opportunities, with skipped slots zero only for
   portfolio opportunity accounting, never for unknown executed returns.
6. Export both arms' true-edge and pending lifecycle logs, whole and two-leg
   ledgers, failure transitions, four halves,24months per arm and full
   distributions. MFE is unweighted held price excursion, not cash profit.
7. Compare paired D over251 and matched excess-change I over154 triples;
   retain97 unmatched excess as unknown, never zero or silently drop them.
   Same seed20260906,9999 calendar-month bootstrap/sign flips. Report SD,
   quantiles, missingness, CI and directional p, no random time splitting,
   outlier deletion, p-value test shopping or observed-power calculation.
   These reused observational months are neither random market assignment
   nor independent validation. p values are exploratory over the V1--V19
   research family. No predictive model AUC; range/ATR single-feature AUC
   and top-decile diagnostics remain descriptive rather than admission gates.
8. A separate saved-ledger verifier must reconstruct two-leg arithmetic,
   masks, events and original populations without strategy imports/raw prices.
   It cannot independently prove raw MA calculations or missing/unlogged edges.

## Acceptance and stop rule

Unchanged gates: net>0,PF>1.1,all4 halves positive,>=80events,>=12/half,
>=12active months,>=3months/half,positive single-position/cost30bp/
leave-top-two results,>=90%matched coverage and positive matched excess.
D/I each require positive mean,95%lower>0 and month p<.01. Known154/251
coverage61.35% cannot pass90%; never lower the gate or discard97 cases.

PRECOMMITTED STOP: If V19 net remains nonpositive OR joint D/I evidence
fails, end pure exit microtuning on this cohort. Do not mechanically test
3/4-bar confirmation or more half fractions next. Return to a separately
frozen audit of entry continuation at an exit-independent observation clock,
with adequate comparable controls, before proposing another such variant.
Prior entry gates already exist: V1 filters, V3 prior4h context, V7 alternate
compression entry, V13 prior colour, V14 support-only breakout. Do not call
them new or claim entry has never been explored. A fixed-horizon entry-edge
audit is a new question, not evidence that a winning indicator already exists.

## Reproduction and delivery

Run .venv/bin/python -m yoyo.evaluation.hourly_impulse_failed_reduce_research.
Refuse existing results; preserve any failure before fixing/retrying. Existing
Python3.9.6,pandas2.3.3,numpy2.0.2,scipy1.13.1 unchanged. No dependencies,
training, ACTIVE/frozen, TradingView writes or live trading actions.
Deliver analysis/p1_btcusdtp_hourly_failed_reduce_v19_20260906.md, immediately
convert with python3 scripts/md_to_html.py PATH --out-dir analysis/html,
and link the HTML. Include all negative evidence and QA limitations. The
profitability goal remains unachieved unless all required evidence succeeds.
