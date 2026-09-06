# V13: prior completed4h colour entry gate

## Decision and authorization

Owner requested continuing implementation toward profitable BTCUSDT.P1h
impulse/engulf-MA-cross entries with lower-timeframe trend exits. V12 completed
a real replay and rejected one extra structural exit; it was progress, not a
blocker and not a profitable system. Test one narrower entry-selection question.
This plan and builder are committed BEFORE any V13 source-price run. Reused
2023--2024 development plus pre2023 warmup only. No2025+prices, holdout, new
dependencies, training, live/TradingView/production change or new repository.

## One independent variable

Keep original V5 requests, K1 extreme initial stops,72h,20bp roundtrip and native
5m SMA40(HL2) true aligned-to-opposite transition exit. Candidate only requires
direction == latest completed4h HL2/SMA40 side available at K1 OPEN signal_time.
Not K1close=decision_time; not developing4h; no future colour, slope, ATR strength,
extension, volume, extra exit, buffers, grid search or tuned threshold.

Aggregate48 complete UTC5m per4h; SMA uses40 contiguous4h HL2, including latest
completed bar. Equality HL2>=MA is +1, else -1. Require context age[0,4h) and same
raw segment through exact5m bar ending at K1OPEN. Missing latest4h cannot silently
fall back;40-bar warmup resets after missing4h. Do not require43-bar slope warmup
or positiveATR to identify pure colour. Each control uses its OWN context.

Sources verified for installed pandas2.3.3; no upgrade:
https://pandas.pydata.org/pandas-docs/version/2.3/reference/api/pandas.merge_asof.html
https://pandas.pydata.org/pandas-docs/version/2.3/reference/api/pandas.Series.rolling.html
Backward asof permits available_at<=signal_time; integer rolling min_periods=40.

## Scope and denominators

Byte-pin original251 cases,462 controls,154 fixed triples and97 unmatched cases.
Never rematch/filter mother identities. Save713 context/gate rows and checkpoint
hash/timestamp BEFORE reading/computing either arm's returns. Rebuild baseline
six ledgers and all saved fields; actually replay selected candidate requests
with identical execution policy, then demand every accepted old field matches.

Three states:

- accepted: actual original execution outcome, including unknown if censored;
- known opposite abstain: noentry/noexit/noexecution/nofee, observed net0;
- unknown context: no asserted trade or successful avoidance; net/feeNaN.

Full episodes remain251/462. Unknowns are never zeros, not dropped from counts;
reported means conditional on known outcomes clearly state known/total. Actual
trade metrics exclude abstentions. Single-position processing still retains all
mothers; knownabstain releases at motherdecision, unknown reserves old72h
conservatively, not a claimed live position. Verify baseline0skip and candidate
occupancy. A handled mother is not necessarily a trade.

## Estimands and experiment design

Fixed all-opportunity D_i=Ycandidate_i-Ybaseline_i on251 rows. Fixed-triplet
I_i=Dcase_i-mean(Dcontrol_i1..3) for original154 groups. Each own rejected control
contributes0; any unknowncontrol or unknowncase makes entireI unknown. Original
97 unmatched remainunknownI. I is a joint-policy comparison against existing
same-symbol/time/volatility matched random entries, not isolated K1 morphology,
not randomized causal proof of4h trend. No treatment-related support change.

PrimaryD/I must jointly show positive mean,95%month-cluster lowerbound>0 and
one-sided positive sign-flip p<.01,9999draws seed20260906. Same-row month blocks
preserve pair/triple relationships; noIID or independent confirmation claim.
Report n/unknown,mean/median/SD/IQR,CI,p,positive/zero rates,monthlyautocorrelation.
Fixed available sample, no post-hoc power hunt, outlier deletion or normality
test shopping. Repeated historical experimentation remains exploratory.

All inherited gates remain: actual trades>=80,min12perhalf,4positive halves,
PF>1.1,12active months,min3perhalf,net andexcess uncertainty,90%matched coverage,
positive single-position economics,extra10bpstress,leave-best-two. Actual trade
count/month/PF/coststress cannot be padded with nontrading0s. Actual coverage
61.35%maximum already misses90%; no candidate acceptance or independent fresh
claim even if selection locally improves. Full opportunities plus selected-trade
economics reported side-by-side with correspondingcontrols. Singlefeature
range/ATR AUC andtopdecile gross/net remain descriptive, no modelvalAUC invented.

## Failure analysis and validation

Paired per-population ledgers count avoidedlosses AND missedwinners, aggregate
event-bp cost, retained outcomes,unknowns; allstates shown, not curated examples.
Candidate classifiedtrades retain existing failedlaunch/costflip/giveback
taxonomy; report classifications cannot become same-round filters. Fourhalves,
24months,long/short and originalmatched/unmatched support help interpretation.

Synthetic checks:39vs40bars,zeroATR/flatMA,HL2notclose,equality,longshort,4phase
availability,K1OPENvsclose,futureprice mutation,prefix invariance,gap reset,
unknownvsabstain,fees,alloriginalidentities,owncontrolgate,fulltriplemissing,
serialknownzero/unknownoccupancy. Independent saved-only stdlibverifier checks
hashes/sourcecommit/checkpoint/time/gates/economics/D/I/serial; explicitly does
not prove40bar rawprice recomputation or independent statistical p.

## Output and chart contract

Deliver Chinese answer-first Markdown plus canonical portableHTML, summary and
completeCSV evidence. Optional saved-only auditnotebook is supporting artifact;
ifJupyter unavailable execute codecells plainPython and disclose the gap. Use
shared official portable renderer, not bespokeHTML or a secondchartlibrary.

One distribution chart answers how often the policy changes outcomes on all251
opportunities, with fixed signedbp bins inclzero andunknown preserved. Native
bar chart SQL aggregation fromcase_delta, oneblue root, signed textlabels,
no redundantcolourlegend. Neutral title, subtitle2023--2024/bp/known-total;
all251 underlyingrows kept withID/date/fold/old/new/D. Exactnumbers in tables.
QA completecanonicalblocks/sourceidentity/actualSQLdataset/semanticHTML; report
any missing enhanced-browser/mobile visualcheck honestly.

## Reproduction

From repository root after committing builder/config/tests/plan:

```bash
.venv/bin/python -m pytest -q tests/test_hourly_impulse_prior_colour.py tests/test_hourly_impulse_prior_colour_research.py
.venv/bin/python -m yoyo.evaluation.hourly_impulse_prior_colour_research
.venv/bin/python scripts/verify_hourly_impulse_prior_colour_v13.py
```

Runner refuses existing results; preserve attempts rather than overwrite. Full
presentation commands, sourcecommit/runtime/output hashes and tests added to
final report. Exact next question only after evidence, not predeclared winners.
