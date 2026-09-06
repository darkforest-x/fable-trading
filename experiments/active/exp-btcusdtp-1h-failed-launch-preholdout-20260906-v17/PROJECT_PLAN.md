# V17 — Before-partial fast reversal: full versus half realization

## One frozen switch and bounded deduplication

Continue the owner's profitable-system research goal, not a guarantee of profit.
V16 improved full251 mean by1.4684bp but remained negative. Test only
fast_failed_launch_exit=true against the exact V16 candidate baseline.
No new entry gate, moving-average length, original K1 stop, cost, duration,
R threshold, partial fraction, control match or direction filter.

Before any partial, consecutive valid completed native5 SMA40(HL2) observations
must change aligned to opposite. Latest completed native15 SMA40 must be valid
and aligned. At the next executable raw5 OPEN, gross>0.002 still realizes50%
of original notional as V16. Its exact complement gross<=0.002 now closes100%.
After a partial, keep V16 slow15 remainder exit and original stop/deadline.
Unknown/opposite slow state does not qualify; no fallback to an older aligned
observation. Rejected edges are consumed; without a new true edge, crossing
the price/profit threshold alone cannot close anything.

This is NOT restricted to the first60 minutes and does NOT mean price never
advanced. A late reversal after prior0.5R/1R progress or profit giveback also
qualifies. Name failed_launch is a policy identifier, not a causal diagnosis.
V1 opposite-state5m, V5 unconditional trueflip5m, V2 1R/2R staged111-entry
slope cohort and V11 60min/0.5R deadline do not exactly implement this branch.
It is a new combination of known mechanisms, not an independent discovery.

## Clocks, accounting and source boundary

Initial opposite/unknown fast state must first align. Fast and slow states
reset independently on invalid/noncontiguous source; only completed bars and
their preceding39 contiguous HL2 observations determine their SMA colours.
Priority remains source/invalidopen, gap K1 stop, slow full exit,72h deadline,
fast full-or-half at open, current later HLC/intrabar remaining stop. No future
HLC can cancel an already-observable open fill. Same-open observation/fill
assumes zero extra execution delay and must be disclosed in the result.

The economic test uses the existing Decimal quote-price expression; equality
belongs to full exit. Only this new full-exit payoff uses Decimal direction*
(exit-entry)/entry before conversion to float, so exact20bp minus20bp is zero,
not a spurious positive epsilon winner. Existing V16 path/accounting bytes
remain unchanged when the switch is absent/False. Half/final cost weights sum
to one20bp roundtrip; a new full exit also pays20bp once.30bp stress re-costs
the same fills; it does not move the fixed20bp branching condition.
Unknown outcomes remain unknown even if the other arm has a known full exit.

Only reused2023--2024 development price prefix, four chronological half-years
and original72h fold embargo. No2025+ price scores, audit or holdout; holdout0.
Retain original251 cases/462 own controls/154 triples/97 unmatched. Controls
use their own colours and original ATR-scaled stops. No random time split or
selection of the retrospective128/161 nonpartial group. The31 eventual partial
paths with earlier rejected edges are counterexamples to inspect, not a new
case subset or an assumed count of V17 changed paths.

## Execution and verification order

1. Commit engine/builder/config/plan/synthetic tests before any new raw replay.
2. Pin V4 mothers/assignments and V5 pre-entry contexts. Regenerate entries
   and contexts; freeze713 fast and1426 slow arm/population initial states
   BEFORE hashing/reading V16 outcome files in the runner.
3. Reproduce V16candidate six ledgers in the OFF arm, every old column. ON and
   OFF share original entry/stop/risk/context exactly; no-trigger paths retain
   every old field. Fast-full paths must have0 partial,100% remaining, known
   full payoff and cannot exit later than the unchanged baseline path.
4. Replay ON for every case/control. Recompute per-arm serial occupancy; do
   not carry V16's unchanged-final-path/serial assumption into V17. Each side
   keeps all251 source intentions, known skipped opportunities zero, selected
   unknowns unknown. Save both arms' full evaluated fast edge/source JSON.
5. Report reduced losses AND deeper losses/missed recoveries/win-to-loss,
   zero/unknown groups, old partial paths cut and all original denominators.
   Future MFE and old final returns are diagnoses, never entry/exit inputs.
6. D all251, I154 with97 unknown, serial all251; same9999 calendar-month
   bootstrap/sign flips,seed20260906. Report descriptive raw distributions,
   SD/quantiles/missingness and assumption limits, retain every outlier.
   No post-hoc test selection, observed power or winner-only return claim.
7. Independent saved-ledger verifier plus synthetic boundaries, followed by
   technical HTML, full failure tables and inspectable saved-audit companion.
   Saved verification does not prove raw SMA or absent unlogged events.

## Unchanged financial gates and delivery

Net>0,PF>1.1,all4 halvespositive,>=80events,>=12/half,>=12active months,
>=3months/half,positive single-position/cost30bp/leave-top-two results,
>=90%matched coverage and positive matched excess. D and I each require
positive mean,95%lower>0 and month p<.01. Existing154/251=61.35%coverage
cannot pass90%; do not delete97 or lower the gate. Repeatedly explored
2023--2024 data is not independent validation even if these effects improve.
Calendar blocking approximates dependence, not market random assignment.
No ML model: model AUC not applicable; range/ATR feature AUC/top-decile
gross/net and matched controls remain descriptive baselines, not acceptance.

Run `.venv/bin/python -m yoyo.evaluation.hourly_impulse_failed_launch_research`.
Existing results directory is refused; preserve failed attempts. Installed
Python3.9.6,pandas2.3.3,numpy2.0.2,scipy1.13.1 stay unchanged. No installs,
TV writes, live orders, ACTIVE/promote or training. Main report goes to
analysis/p1_btcusdtp_hourly_failed_launch_v17_20260906.md and is immediately
converted to analysis/html/p1_btcusdtp_hourly_failed_launch_v17_20260906.html.
Canonical source-backed report with one useful full paired-delta distribution,
exact audit tables and honest browser/notebook QA limitations. If rejected,
record failure and choose the next hypothesis from full-sample evidence.

## Runtime correction before attempt2 (no strategy revision)

Attempt1 at e5139c5 stopped during OFF case-trades strict saved parity,
before ON simulation. Two partial-fast source IDs had been inferred as
integers by CSV loading, while engine semantics are opaque strings.
Preserve all nine original files and started/failure receipts under
attempts/attempt_01_csv_segment_identity. A synthetic full V16 CSV
roundtrip identifies exactly these two fields; reading-time converters
preserve 0,007,empty,literal nan,0.0. Wrong identities still fail.
Inject a V17-only saved reader; default native reader, generic parity,
strategy config, prices, costs and all gates stay unchanged. Commit this
correction and tests before attempt2. Report two raw attempts, one
pre-candidate failure and one candidate execution, if retry completes.
