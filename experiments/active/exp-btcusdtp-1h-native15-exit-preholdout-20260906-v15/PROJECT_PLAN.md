# V15 — Native 15-minute exit on the original hourly impulse pool

## Frozen decision, before prices or outcomes

Owner requests a profitable hourly impulse/trend system; that is an objective,
not a promise. Compare original V5 direct entries with native5m and native15m
SMA40(HL2) true-transition exits. V14's20-hour breakout gate failed support
(60<80; one half11<12); it is not applied here. No V13 entry gate, V11 progress
deadline, V12 frozen boundary, partial TP, new MA length or parameter sweep.

Native15 changes aggregation, seed,40-bar memory200→600min and decision clock
together. This tests that complete management specification, NOT pure cadence.
Both management frames are recomputed from complete native OHLC, never5m data
relabeled15m. Every hard stop still checks raw5m; same5m ambiguity is stop-first.

## Deduplication and hypothesis

V1 tested opposite-state exits at15m on this251 pool, not true-transition
semantics. V8 tested true native5/15 on another286-request/959-zone population;
V9 sampled a5m series every15m. Bounded source review found no identical native
true-transition contrast on original251 intentions, not proof of global absence.
Prespecify replay of native15 opposite-state semantics on both populations as
diagnostic replication only. If economically identical, say this recovers old
state-exit behavior, not a new edge. This third replay cannot select a policy.

H1: paired case improvement D>0 AND improvement beyond own random-control
improvement I>0, with month-cluster p<.01 and lower95%CI>0 for both. Also require
net positive, PF>=1.1, all4 positive halves,80 trades,min12/half,12 active months,
min3 months/half, serial net positive and>=90% matched coverage. Original154/251
coverage61.35% already cannot meet90%; even positive findings are diagnostic,
not final acceptance. No claim of independent validation on reused2023–2024.

## Population, causality and order

Keep all251 cases/462 own controls,154 fixed non-reused triples,97 unmatched.
No rematching, future-based selection, denominator shift or unknown-to-zero
conversion. Serial accounting recomputes occupation under each exit rule.
Freeze V5 entry/stop/risk at next hourly open,72h and20bp roundtrip cost.
Only2023–2024 price prefix materialized; source boundary audit uses timestamps
outside this prefix, never price columns. Holdout use0; no audit entry point.

1. Commit config, this plan, helper, runner and synthetic tests before real run.
2. Pin V4 input mothers/assignment and V5 entry contexts (no outcome hashing yet).
3. Verify regenerated hourly entries and old5m entry context all-field parity.
4. Independently attach both native5/15 initial contexts for every own request;
   persist all1426 rows and context_frozen.json before ANY outcomes are read.
5. Pin old V5 outcomes and require all original columns across six baseline
   tables; replay candidate on unchanged requests, verify entry/stop parity.
6. Compute D(all251), I(original154;97 unknown), serial(all251 known skips0).
   Month bootstrap/sign-flip9999 draws,seed20260906; no random time split.
7. Describe all winner/loss transitions, exit delay, MFE and fee losses. MFE is
   observed while held, not a future entry feature or proof of latent trend.
   Run the predeclared state15 semantic diagnostic; do not select using it.
8. Independent saved-ledger recomputation, synthetic causality, failure audit,
   official canonical HTML and inspectable notebook. Report limitations.

## Statistical and delivery contract

One fixed pair, fixed month-cluster method; no outcome-conditioned normality
choice. Examine full raw-return/delta distributions before inference; preserve
outliers. Month blocks are an approximation, not independent random assignment;
repeated prior search makes p exploratory. No observed-power claim. No fitted
classifier: AUC not applicable; report single-feature baselines and top-decile
descriptive values with selection/unknown caveats, never replace aggregate loss.

Technical HTML audience: title, technical summary, definitions before evidence,
findings by half and failure transition, method, uncertainty/validation,
recommendation, further questions. Reorder specification definitions ahead of
findings for clarity. Native histogram of all251 paired returns/delta shows
shape; exact result/half/transition tables support lookup (not extra tiny charts).
Source-backed canonical artifact only; no bespoke HTML renderer. Notebook
reuses saved-ledger verifier, not a second independent raw replay. Browser QA
or Jupyter missing runtime must be disclosed; no dependency/browser installation.

## Reproduction and boundaries

Run `.venv/bin/python -m yoyo.evaluation.hourly_impulse_native_exit_research`.
Existing results fail closed; preserve failures and record any rerun explicitly.
No production/model/TV modifications; no trades, risk changes or promotion.
Sources docs: pandas2.3 merge one-to-one and NumPy2.0 Generator.choice; exact
source versions and builder commit pinned in started.json. Financial comparison
does not include funding, latency or tick ordering;20bp is not a full execution
model. Any failure is evidence to record, not permission to lower acceptance.
