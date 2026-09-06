# V10: original hourly-entry matching support, no outcome search

## Frozen question and population

The owner goal remains profitable BTCUSDT.P hourly impulse/engulf MA-cross
entries managed on lower-timeframe trends. V9 rejected exit cadence on the
source-zone branch. Return to the literal original hourly-entry family, but
first establish whether its existing comparison design can cover its population.
This is enabling data-quality research, not a substitute success criterion.

V4 retained251 original K1 mothers with154 complete three-control matches,
94 insufficient-exact-control rows and3 causal-ATR-bucket support gaps. The
matched subset overrepresents direction-aligned hourly slopes. These counts
were observed before this plan from saved pre-entry ledgers; they are not a
prospective discovery. This run will reconstruct previously unsaved candidate
support to distinguish sparse eligibility from allocation losses.

Byte-pin four V4 pre-entry artifacts, original V1 baseline configuration,
251 IDs and four2023--2024 half-year folds. Original mother shapes, direction,
times, K1 extreme risk,3 controls, seed20260906, exact matching and no reuse
stay unchanged. No exit, MFE, PNL, selected-winner or K2-success input is read.
No training, profit filter, audit2025+, TradingView or live mutation.

## Unchanged matching contract

Each maternal decision uses the completed1h signal and actual raw5m open at
that boundary. Its known stop-distance/ATR is transferred to a control's own
entry open and completed-hour ATR. Check transferred stop finite and>0 per
mother-candidate edge; shared strata do not imply complete connectivity.

Exact same month, UTC6h time bucket, causal ATR tercile, latest completed5m
colour, completed1h colour and direction-adjusted hourly slope sign. The ATR
tercile uses the previous720 same-segment hourly values shifted1,minimum168.
No null-key matching. Candidate exclusions use current or preceding completed
hour STRICT body crosses, not future crosses and not just qualifying K1s;
actual maternal decision timestamps are also excluded.

Candidates must be strictly earlier than foldEnd-72h. Do not invent a maternal
plus/minus72h exclusion. The original cumulative pre-key pool is preserved for
parity; actual fold-local and exact-month supply are separately reported.
Same-month candidates later than the mother remain eligible as in V4. Each
candidate's features are causal at its own decision, but full-month allocation
is offline support analysis, not online/prefix-stable trading or random treatment.

No control reuse means the actual candidate timestamp, not timestamp plus
direction. Folds have disjoint decision months; verify no edge candidate spans
multiple folds before solving globally. Original greedy assignment remains the
historical benchmark; a new capacity allocation is not used to rescore returns.

## Ordered audit and capacity certificate

1. Commit config/plan/all builders/synthetic tests before raw-price reconstruction.
   Verify all source/input hashes and the physical timestamp boundary first.
   Read the same development prefix including pre2023 feature warmup, never
   materializing prices at or after2025-01-01. Holdout consumption0.
2. Regenerate all original mothers and compare EVERY saved field. Reconstruct
   the complete matching frame. Reproduce154 matched/462 controls, every old
   assignment/control field and four assignment hashes/receipts; abort before
   capacity inference on any discrepancy.
3. Persist all mother keys and support flags, full matching frame, candidate
   stages, per-key supply, per-mother stage counts, preallocation valid edges,
   original used-before/selected-edge history. Preserve missing values as such.
4. Solve max(sum y_m), with binary mother y_m and edge x_mc:
   sum_c x_mc=3*y_m, sum_m x_mc<=1. Only originally valid edges may exist.
   This maximizes complete triplets, NOT total edges or a rounded relaxed flow.
   Require optimal status, integer feasible allocation, consistent objective,
   dual bound/gap, no orphan/duplicate edges. Independent connected-component
   capacity bound and synthetic brute-force cases cross-check the solution.
5. Time limit30seconds; a nonoptimal status is not proof of unattainability.
   Preserve failure artifacts and report the unresolved certificate, do not
   silently rerun more seeds or call a partial incumbent the maximum.
6. 90% of251 requires226 matched. Report maximum minus original154 as recoverable
   allocation loss, not earnings. If maximum<226, the strict support design is
   insufficient even with perfect allocation; do not lower90%, reduce controls,
   reuse timestamps or relax keys to obtain a passing number.

## Interpretation and controls

Missing covariate support, raw exact-cell shortage, crossing/actual-mother
exclusions, invalid transferred stops and greedy depletion are separate stages.
Counts are an ordered descriptive decomposition, not randomized causal effects
of removing each rule. Some exclusions overlap. A mother unselected in one
maximum assignment is not necessarily impossible in every maximum assignment.

No new financial outcomes are computed, so valAUC, net/gross/top-decile returns,
win rate and profit-ranking permutation p are inapplicable. Equivalent strict
null controls: inject arbitrary outcome columns without changing audit results;
rename/shuffle graph identities without changing capacity; compare MILP to
exhaustive small-graph truth, including a greedy-failure and shared-bottleneck
counterexample. Existing financial failures remain in their unchanged reports.

If strict support is insufficient, separate all251 same-event strategy contrasts
from conditional excess on the supported subset. Do not extrapolate the latter
to all251. New months/target populations require new frozen designs; they cannot
fill a missing cell in an old month. No particular entry filter is nominated here.

## Deliverables and sources

Reproducible audit module, pre-entry ledgers and capacity certificate, independent
saved-graph verification, failure/coverage report MD then HTML, inspectable
companion notebook, registry hashes, tests and learning note. This is research
only; owner profit goal remains active and unachieved.

Existing runtime: pandas2.3.3,NumPy2.0.2,SciPy1.13.1,matching constraints-ci.txt.
No dependency installation. Official API semantics:
https://docs.scipy.org/doc/scipy-1.13.1/reference/generated/scipy.optimize.milp.html
https://pandas.pydata.org/pandas-docs/version/2.3/reference/api/pandas.DataFrame.merge.html
These sources document implementation, not a profitable strategy.

```bash
git branch --show-current
.venv/bin/python -m pytest tests/test_hourly_impulse_matching_support.py tests/test_hourly_impulse_matching_capacity.py tests/test_hourly_impulse_support_research.py -q
.venv/bin/python -m yoyo.evaluation.hourly_impulse_support_research
```

The runner refuses existing results. Reproduction must retain old attempts and
register a new output attempt; never delete historical evidence to make it run.
