# V14 support-only verification and delivery

## Result and scope

This stage is complete, inconclusive on profitability and rejected for entry to
the preregistered outcome stage. Overall goal remains active and unmet, not
blocked. One actual source run after builder5fc542b, start2026-09-06
09:43:01.361945UTC, all source/support rows frozen09:43:03.238671UTC before any
outcome calculation. There is NO outcome calculation/replay in this runner.

Original251cases:60accepted191abstain0unknown;462controls:53/409/0. Casefolds
17/15/11/17, acceptedmonths23, min5perfold. Gates80/min12 failed60/min11;
months12/min3passed. Full original154three-control groups/97unmatched retained,
all154knownsource butcoverage61.35% stillbelow90%, separatelynotaccepted.
No previous V5/V13 outcome CSV or summary hash/read by this support experiment.
Original V4 pre-entry mother/controls/assignments/assignmentreceipt only.

Sourceprice prefix219551 rows through2024-12-31 23:55UTC; archive341567 timestamps
through2026-02-28 used for boundary/hash only. No2025+ price materialization,
holdout0. Snapshot713 gates plus14973 saved hour rows,62count rows,154triples.
No new dependencies, model training, TV/production configuration, live API or
real-money action. No PF/AUC/p/winrate computed; no profitable candidate claim.

## Independent saved-source-window verification

Verifier and presentation builders committedbbc1c63 before actual saved
verification/notebook/artifact generation. `saved_verification.json` passed:
four CSV hashes,11 committed builder source receipts/time, complete old mother
field parity, fixed assignments, own21-hour source windows, current K1 exclusion,
prior20extrema, closeparity, strict directional gate/equality, count denominator,
all62dimension rows including48months, same154controls and zerooutcome claims.
Originalcasebreakout20 flags independently agree with all60newaccepted cases.

The verifier recomputes max/min from SAVED complete hourly source rows. It does
NOT reaggregate raw5m or prove independently that external exchange OHLCV are
correct. Raw5/hour source counters are separate and never numerically equated.
Synthetic source aggregation/future/gap tests cover implementation boundaries,
not external market authenticity or profitability. Same physical hour must be
consistent across all duplicated request snippets.

## Test commands and actual results

```bash
.venv/bin/python scripts/verify_hourly_impulse_prior_breakout_v14.py
.venv/bin/python -m pytest -q tests/test_hourly_impulse*.py tests/test_verify_hourly_impulse*.py tests/contracts/test_registries.py tests/boundaries/test_layer_imports.py
```

Final relevant suite3057passed44.02s. V14 helper54,runner17,verifier79,report+
notebook28 new synthetic tests included; do not sum overlapping earlier runs
as extra independent evidence. Before builder150related passed4.46s. Initial
registry check caught missing required `result`; prereg result/canonicalplan
were added before builder/source. No gate or financial expectation was relaxed.
Unscoped diffcheck also exposed unrelated existing CRLF whitespace in other
research receipts; those files were untouched, not claimed globally clean.

## Notebook and canonical HTML

`prior_breakout_audit.ipynb` has3actual code cells run top-downplainPython,
minimalnbformat4.5 structure andcodecompile checked. All4savedCSV+summary and
V14/V11verifier modules hash-pinned. It reuses SAME verifier.verify_tables,
not a second independent implementation. No raw or outcome reads. Jupyter
kernel/fullnbformat schema notrun:nbformat/nbclient/ipykernel unavailable;
no dependencies installed. Full reproduction commands in final sourceMD.

Report has10peer sections+title+1nativechart=12blocks. Actual SQLite fold query
returns4rows with original251case denominators,60accepted,191abstain0unknown,
own rates. It is a blue count comparison, not PNL/performance chart. Fullreport
definition/method/limitations/next/reproduction sections retained. Combined
claims have explicit evidence links; sourceId on counts-only section/chart.

Repository MD-to-HTML conversion immediately followed MD creation/changes.
Then officialDataAnalytics0.2.10 canonical artifact renderer produced same
HTML delivery. Final `portable_qa.json`:validation/packagepassed,
verificationstructural_only; no installedChromium, mobile/source-dialog or
interaction QA NOTrun. No browser installed or bespokechartHTML.
One targeted MD narrative update was patched into samefullartifact while
asserting identical snapshot, chart and blockIDs; no data/financial rerun.

## Artifact identities

| Artifact | SHA256 |
|---|---|
| config.json | 223b9c91946b00aa70450d64fd3bddcf0fc92a6332bbc1322b8352c5505a5fe0 |
| results/summary.json | 1a6f1d64ec4448d756a37f659346cea26486c1e7923eb3998985fb5a793924f4 |
| results/entry_context.csv | 8dc28d72b55088da1017aacbbc492e525f6fa3ccc527a9b7f8e39e4f5d906a54 |
| artifact.json | a8fed4b3220728357cdf78fce7192029b3899176ea2a80b1f57295dd7e78a38c |
| prior_breakout_audit.ipynb | 3ed662b09551bd2603d8c611e4650c2244b41989bb670aa5301b138ef5a359a9 |
| saved_verification.json | 3271fecf12575ff8022637ac9c75632347cc558125ee609e045e2fd9ff5f3ec6 |
| source MD | c0906af323a3e49fe8ccf1902a3b8ce0cbccf6f6d4031310a426619a4d359f68 |
| portable HTML | eb2919df85896d28173413f979a4f9df48622f20544c57126dc0db409ca96b4c |

## Skills and next step

Experimental design fixed support stopping before outcome ranking. Source-driven
development checked installedPython3.9.6/pandas2.3.3/numpy2.0.2 and official
pandas rolling/shift docs. Dataquality/validation enforce window+denominator
audit;build-report/visualize/jupyter enforce full canonicalHTML and honest
supportnotebook. Documentation/learninglaw record this atomic negative lesson
in `docs/learnings/entry-gate-support-should-precede-outcome-ranking.md`.

Next note is bounded native15m exit deduplication, not another implemented/run
experiment. V1 already had15mstate on original251;V8 hadnative15mtrueflip on
different286casepopulation. First freeze both initial states before any new
same251trueflip contrast; identical15mstate results must be called replication.
Overall profitable validated system remains unachieved. No automatic promotion.
