# V11: Fixed launch-progress deadline

## Decision and scope

Owner authorized continued implementation toward net profitability. This is one
finite research mechanism, not a profitable-system claim or deployment request.
Only reused2023--2024 BTC-USDT-SWAP development prices plus pre2023warmup are read.
No2025+ price materialization, holdout use, new fitting, TV write, ACTIVE change,
VPS writer, live order, dependency change, branch or worktree. Builder/config and
tests must be committed before the first real outcome run. Existing results are
never overwritten; exceptions persist a failure receipt and stop.

## Single mechanism and clocks

Baseline is V5 original251 direct1h K1 entries with native5m SMA40 true aligned-to-
opposite colour transition exit. Not V7 source-zone release, not V5 opposite-state
exit. Preserve real entrytime/price/direction, K1 extreme initialstop, initialrisk,
20bp roundtrip, raw5risk clock and mother+72h maximum. Add exactly one preset:

- At entry E freeze R=direction*(entry_price-initial_stop).
- Observe complete postentry raw5 CLOSE at E+5,...,E+60 inclusive. If any signed
  close progress >=0.5R, permanently cancel this deadline for that trade.
- If no such progress and still open, close fully at real raw5 OPEN at E+60,
  launch_timeout_exit. Do not use high/low/open as proof of progress.
- E+60 completed close can cancel. Previous-bar initialstop, current-open gap
  stop, original pending colour/totaldeadline exits retain priority. Future low
  of the new E+60 bar cannot stop an already market-closed position.
- Raw missing/invalid OHLC or broken raw clock is unknown/censored, never fake
  no-progress or synthetic nextopen. Mere management colour NaN preserves its
  original transition-reset semantics, independent from valid rawclose progress;
  it neither erases reached progress nor gives an automatic timeout exemption.
- No reentry/partialprofit/stopraise/rearming after progress. Fixed60min is one
  signalperiod;0.5initialR is a preselected halfrisk hypothesis, not an optimum.
  No30/45/90min or otherR grid, no parameter repair after seeing outcomes.

This differs from V2 after1R/2R partialprofit, V5 actualtransition, V6 beforeentry
waiting and V8/V9 slower management. Bounded historical code/report search did
not find this exact cumulative completed-close rule; not an exhaustive proof.

## Evidence and population

Byte-pin V4 original251mothers,462controls,oldassignments/receipt and nine V5
context/baseline outputs. Original154triples stay fixed; never use V10 maximum
allocation. Rebuild case entries and both completed entrycontexts, then replay
baseline case/control. All saved trade,episode,matched,singlepending columns
must agree (timestamps exact, CSV floats1e-12); only then run candidate outcomes.
All old request fields and risk/entry fields must agree across candidate/anchor.

V10 proved exact complete capacity154/251=61.35%, below90%. The finite mechanism
test can still measure all251 paired strategy changes and conditional154 relative
changes, but cannot nominate/accept this whole population regardless of returns.
No unsupported control becomes zero and no future winner/MFE filters an entry.

## Hypotheses, inference and stop rule

Primary D251=new-case minus old-case net on every original mother. Secondary
co-required I154=D_case minus mean(D_three assigned controls), each control's
own frozen initialrisk and same rule. Unchanged pairs retain zeros; missing
remain unknown, with denominator/attrition explicit. Joint positive evidence
requires both D and I positive, one-sided month-block p<.01 and95% bootstrap
lower>0; conjunction, not choosing whichever p passes.9999 draws,seed20260906.
No new family of exploratory gate searches. Sample size fixed by old251/154
support, not power-chasing; report uncertainty, no equivalence claim from p>.01.
Development reuse/sequential selection/month boundary dependence prevent
confirmatory interpretation even if these approximate conditions pass.

Report both net levels, gross, PF,wins, four halfyears,24months, matching levels
and excess; fixed single-feature range_ATR AUC/topdecile gross/net is descriptive,
not a trained model or a new feature gate. Preserve20bp plus inherited extra10bp
stress, leave-top-two, old80/12sample,4positivefold/PF1.1/12month/90%coverage gates.
No audit if rejected. No new threshold if accepted diagnostically: future fresh
independent evidence is still required. Previously examined2025+ is not pristine.

Save all251 paired mechanics with actual entry/exit/stop/fee/hold and progress
clock fields. Cross-tab losses-to-wins,wins-to-losses,retained losers/winners,
timeoutaffected vs originalexitretained. Affected means postentry outcome
diagnostic, never selection population. Require every changed known case to be
a60min timeout strictly earlier than baseline; all other known economics/time
unchanged. Retain all extremes and unknowns. Distribution quantiles/SD/IQR and
monthly mean autocorrelation assess tails/dependence without switching tests.
Single-position replays retain all251original intentions, with skipped known0
and selected unknownNaN, plus accepted trade metrics; event sums are not equity.

## Verification and delivery

Synthetic long/short boundary,threshold equality/nearby,wick-only progress,
E+60/E+65,earlierprogressgiveback,rawmissing,managementmissing,stopcollision,
cutoff and defaultpolicyparity tests before real outcomes. Independent saved-
ledger cost/clock/identity/pair/serial verifier, not claimed rawprice secondrun.
Write Chinese MD and generated portableHTML, source JSON, provenance receipts,
companion notebook and learning note. No chart parameter should change a test.
Report confidence separates verified arithmetic from strategy uncertainty.

Reproduction after committed builders, from repositoryroot:

```bash
.venv/bin/python -m pytest tests/test_hourly_impulse_launch_deadline.py tests/test_hourly_impulse_launch_research.py -q
.venv/bin/python -m yoyo.evaluation.hourly_impulse_launch_research
```

Prior outcome directories are preserved. Reruns need an explicitly new attempt
with lineage, not deleting existing results. No system-wide installs.

## Source timing and implementation references

TradingView market-order clock documentation is a timing analogy, not a claim
of Python/Pine emulator parity:
https://www.tradingview.com/pine-script-docs/concepts/strategies/#order-creation-and-execution
https://pandas.pydata.org/pandas-docs/version/2.3/reference/api/pandas.testing.assert_frame_equal.html
https://numpy.org/doc/2.0/reference/random/generated/numpy.random.Generator.choice.html
Actual runtime verified Python3.9.6,pandas2.3.3,NumPy2.0.2,SciPy1.13.1. No dependency change.
