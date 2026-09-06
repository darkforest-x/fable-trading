# V12 verification — frozen own signal-hour MA exit

## Decision and provenance

Rejected as a profitable candidate. The active profitability goal remains
unachieved. No TradingView, training, production switch, live order or deployment.
Only reused2023--2024 development plus prior warmup; holdout consumed this run:0.

- Builder/config/plan/engine/tests/facts/report/notebook: `c5ce2f26287cd3230d98f02913065cdf5c86f107`.
- First replay checkpoint: `2026-09-06 08:37:23.297529+00:00`, after that commit.
- Independent saved-ledger verifier: `cafc14d`.
- Config SHA256: `0830a19026a302032f40fb6c02534d19a125ab73a82cbded0be48cfd78bbbea4`.
- Summary SHA256: `a0e57d7c235feab76135828f8b2067b840c7600b7a543af022571cad44fb8a3a`.
- Source archive SHA256: `767f67c2b0ae5a8c83369a7cb950334e61de09edbb82a0158122c41794eed5ac`.
- Phase price prefix219551 rows, last `2024-12-31 23:55 UTC`. Physical archive341567 rows, last timestamp `2026-02-28 15:55 UTC`; later timestamps/hash preflight only, not later price materialization.
- Summary pins52 other output files,53 results files total. Facts pins five descriptive CSVs. One actual price replay; no failures/retries or result edits.

## Actual preflight and replay sequence

1. Commit and fixed source/input/config checks before Study. All original251
   cases/462 controls,154 fixed triples; unmatched97 never silently removed.
2. Exact own signal_time join to recomputed60m SMA40(HL2) and signal close before
   any outcome.251/462 MA and close checks pass; max MA CSV error1.4552e-11 price
   units, close error0, relative/absolute tolerance1e-12. Saved inputs not replaced.
3. Pre-outcome713-row geometry checkpoint at08:37:24.513734 UTC. All251 cases
   0<g<1; controls10 negative,190 inside,262 beyond_stop. All five fixed bins
   included even when empty. No boundary-distance filtering or rematching.
4. Original mothers/contexts regenerated, then full original V5 baseline six-table
   parity before candidate. Dimensions: case trades251x67, control462x76,
   episodes251x37/462x46, matched251x7, serial251x39. Every old column retained,
   exact times and1e-12 CSV float tolerance.
5. Candidate only adds frozen_ma_exit=True. Same entry/K1stop/native5m trueflip,
  72h and20bp. Both713-trade arms fully closed, no rejection/censoring. Serial
   accepts251 original intentions and251 trades in each arm,0 occupancy skips.

## Tests and independent saved-ledger audit

Pre-run new module/registry scope:365 passed. Final combined command:

```bash
.venv/bin/python -m pytest tests/test_hourly_impulse*.py tests/test_verify_hourly_impulse_support_v10.py tests/test_verify_hourly_impulse_launch_v11.py tests/test_verify_hourly_impulse_frozen_ma_v12.py tests/contracts/test_registries.py -q
```

**2534 passed in32.74s**. This is the hourly research/registry regression scope,
not every repository test. New engine180, runner112, facts7, report17, notebook33,
verifier117. Synthetic tests need no historical price file.

```bash
.venv/bin/python scripts/verify_hourly_impulse_frozen_ma_v12.py
```

Actual saved audit passed.52 output hashes,18 source identities via original git
commit and commit-before-run;1426 trade formula/risk/cost/clock checks; exact old
baseline and contexts; fixed parents/triples;251 D/serial and154 known I with97
unknown;713 geometry equations and48 whole-cohort arm-month rows. Restored both
source schemas from asymmetric merged fields; candidate-only fields remain
unsuffixed. All unchanged trades preserve every old output field.

Audit reconstructs D=+0.3573426116591334bp (10 improved,10 worse,231 unchanged),
I=+1.3436434193255111bp (9 improved,11 worse,134 unchanged,97 unknown), serial D
same as all cases. Frozen exits:20case/10control. Three untrimmed distributions
for each population, fixed five-category geometry and52 output identities checked.

Limits explicitly returned: `raw_replay=false`, `inferential_p_recomputed=false`.
This is saved-ledger consistency, not a second raw replay. It does not prove the
first raw CLOSE was not missed, intrabar stop path, actual source segment truth,
independent hourly MA recomputation or bootstrap/sign-flip p values. The runner's
source parity receipt is checked as a declaration; only the main runner rebuilt
those hourly inputs. Hash identity is not proof of underlying market data truth.

Before first facts generation, review corrected asymmetric missingness in paired
means and direction-labeling of extreme examples; seven synthetic tests include
one-arm unknown and all-one-sign changes. No price or strategy result changed.
See paired-failure-tables-need-paired-denominators learning.

## Financial reconciliation and failure mechanisms

- Cases: net-14.30663 to-13.94929bp,PF.62471 to.63062,wins62 unchanged,losses189 unchanged.
- D95%CI[-.33977,+1.07344]bp,p=.1846; candidate netCI[-21.99470,-5.36289]bp.
- Matched154 cases-.00118148 to-.00116707 fractional return; mean net-11.81482
  to-11.67068bp. Controls-20.96135 to-22.16086bp. Conditional excess+9.14653
  to+10.49018bp; ICI[-.39954,+3.75043]bp,p=.1324. Most I gain is control damage.
- Four candidate half-years-12.35796/-24.59003/-9.85133/-8.75758bp: all negative.
-20 added exits all remain losses: mean-79.92954 to-75.44489bp;17 original colour
  and3 original hard stops.31 case triggers include11 original colour priority
  exits at the same timestamp.20 changed exits lead5--70min,median20,mean23.
- Cases11hard stops/20frozen/220colour exits; final losses161 gross nonpositive
  and28 small positive gross returns consumed by20bp. Overlapping35 MFE>=1R
  loss flags are not the mutually exclusive14 giveback taxonomy labels.
- Control10 added exits contain3 prior winners changed to losses. All three
  were in pre-known negative-g geometry. No control is deleted or rearmed.
- Coverage154/251=61.35% cannot pass90%; net/PF/fourfold/stress/inference gates
  also fail. No best-looking threshold selected, no promotion.

## Report and notebook delivery

Owner report: `analysis/html/p1_btcusdtp_hourly_frozen_ma_v12_20260906.html`.
Source Markdown has complete exact rules, case/control tables, same-support
effects, pre-outcome geometry, failure examples and explicit next proposal.
`artifact.json` is the canonical full source/manifest/snapshot artifact.

Official portable receipt:

```json
{"ok":true,"stages":{"validation":"passed","package":"passed","verification":"structural_only"},"counts":{"blocks":15,"charts":1,"html":0,"metrics":0,"tables":0},"sourceDialog":"not_verified","sourceInteraction":"not_verified","viewports":[]}
```

One actual SQLite fixed-bin count chart includes all251 D values, zero atom and
tails; not density. Financial tables are Markdown inside canonical blocks,
hence manifest table count0. No compatible Chromium headless shell installed;
no browser/mobile/touch/source-dialog visual claim, no browser installation.

Notebook: `analysis/output/btcusdtp_hourly_frozen_ma_v12_20260906/frozen_ma_audit.ipynb`.
13 cells,5 code cells actually executed top-down in plain Python. Pinned summary
and three allowlisted CSVs;251D,502case economics/costs,20frozen/31recorded triggers,
713geometry/154matched/97unmatched independently reconciled. Does not calculate
control PnL,I,p or raw path. Minimum nbformat4.5 fields/unique IDs/compilation pass;
not Jupyter-kernel execution or full schema validation (nbformat/nbclient/ipykernel
unavailable). No dependencies installed. Its header accurately states this gap.

Independent report review reconciled major numbers, matched denominators, all
three prose examples and the overlapping-versus-exclusive loss labels. A geometry
section mixed an entry-only source with a joined outcome observation; that sentence
was removed from the entry-only section and its causal-sounding title narrowed.
Markdown/canonical artifact/HTML were regenerated consistently with the original
snapshot timestamp. Financial results and distributions were not changed.

## Reproduction

Existing pinned venv: Python3.9.6,pandas2.3.3,numpy2.0.2,scipy1.13.1. First-run
builders must be committed. Research/facts/notebook/artifact refuse overwrite;
preserve old evidence and use an explicitly registered new attempt if replaying.
Do not delete outputs or rerun prices to refresh prose.

```bash
.venv/bin/python -m yoyo.evaluation.hourly_impulse_frozen_ma_research
.venv/bin/python -m yoyo.evaluation.hourly_impulse_frozen_ma_facts
.venv/bin/python scripts/verify_hourly_impulse_frozen_ma_v12.py
.venv/bin/python -m yoyo.evaluation.hourly_impulse_frozen_ma_notebook --output analysis/output/btcusdtp_hourly_frozen_ma_v12_20260906/frozen_ma_audit.ipynb --check
.venv/bin/python scripts/md_to_html.py analysis/p1_btcusdtp_hourly_frozen_ma_v12_20260906.md --out-dir analysis/html
.venv/bin/python -m yoyo.evaluation.hourly_impulse_frozen_ma_report --markdown analysis/p1_btcusdtp_hourly_frozen_ma_v12_20260906.md --summary experiments/active/exp-btcusdtp-1h-frozen-ma-exit-preholdout-20260906-v12/results/summary.json --case-delta experiments/active/exp-btcusdtp-1h-frozen-ma-exit-preholdout-20260906-v12/results/case_delta.csv --output experiments/active/exp-btcusdtp-1h-frozen-ma-exit-preholdout-20260906-v12/artifact.json
node /Users/zhangzc/.codex/plugins/cache/openai-curated-remote/data-analytics/0.2.10-13ceeea1f599/skills/build-report/scripts/deliver_portable_artifact.mjs --input experiments/active/exp-btcusdtp-1h-frozen-ma-exit-preholdout-20260906-v12/artifact.json --output analysis/html/p1_btcusdtp_hourly_frozen_ma_v12_20260906.html
```

The next proposal is documented in NEXT_EXPERIMENT: only prior completed4h side
as an entry gate, no hidden slope warmup gate, all251 opportunities retained.
Prior slope and joint4h-slope/colour tests were located; pure4h-side on V5 trueflip
has not been found. This next proposal has NOT run. Independent validation and
matching-support shortcomings remain; no profitability claim.
