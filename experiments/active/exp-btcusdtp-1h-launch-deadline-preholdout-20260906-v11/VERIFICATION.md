# V11 verification — fixed 60min / 0.5R launch deadline

## Decision and provenance

Rejected mechanism; profitability goal remains active and unachieved. No TradingView,
production, training, deployment or real-money change. This is reused development,
not independent validation. Holdout price consumption: 0.

- Builder/config/plan/engine/tests committed in `33f31f025f342f706dd4f0ea7ed9852891167ebb` before the first replay at `2026-09-06 07:45:20.496435+00:00`.
- Facts/report/notebook builders: `d0c583b`, committed before their first execution.
- Initial independent verifier: `078211b`; asymmetric-column correction: `2c332ed`.
- Config SHA256: `8e7d638f16e75bdb3ef6a5c56b5de20601832b22ca4cd4050bb0a11598c06ffe`.
- Summary SHA256: `36048749c4cdc22ccde3fecd0b6158dc89a669590418bc3528cd1334191ffc12`.
- Source archive SHA256: `767f67c2b0ae5a8c83369a7cb950334e61de09edbb82a0158122c41794eed5ac`.
- Price prefix: 219551 rows including pre-2023 warmup, last open `2024-12-31 23:55 UTC`. Physical archive: 341567 rows, last timestamp `2026-02-28 15:55 UTC`; timestamp/hash preflight only beyond the development prefix, not later price materialization.
- `results/summary.json` pins 49 other output files; 50 result files total. `facts/facts.json` separately pins the descriptive derived tables. No results were changed during verifier or prose correction.

## What actually ran

1. Fixed original V5 direct-K1 requests, 251 cases and 462 controls. Exact original 154 groups of three controls; 97 unmatched remain unknown for excess, not zero. No V10 reassignment.
2. Regenerated all causal entry/context fields; original baseline all-column parity passed before candidate execution. Saved six-table dimensions: case trades 251×67, case episodes 251×37, control trades 462×76, control episodes 462×46, matched 251×7, single pending 251×39. Exact timestamps and `1e-12` float CSV tolerance; no dropping old columns.
3. Both arms kept entries, K1 hard stops, 20bp round-trip cost, 72h limit and native5m SMA40 true-colour transition exits. Candidate alone adds a 60min deadline unless a completed held5m CLOSE previously reached 0.5 initial R. Controls preserve each matched case's risk/ATR, scaled to their own ATR/entry, not an independently chosen stop.
4. 251+462 trades closed in each arm, no rejected or censored trades. All 251 original serial intentions accepted in both arms; no occupancy-based selection.
5. Saved all cases, controls, episodes, matching, serial ledgers, changes, monthly tables, failure diagnoses and retrospective examples. Main replay was run once; verifier fix did not trigger another price replay.

## Test and independent ledger verification

Final targeted regression command:

```bash
.venv/bin/python -m pytest tests/test_hourly_impulse*.py tests/test_verify_hourly_impulse_support_v10.py tests/test_verify_hourly_impulse_launch_v11.py tests/contracts/test_registries.py -q
```

Result: **2068 passed in 26.71s**. This is the hourly-impulse research and registry test scope, not the entire repository. New launch engine tests: 218; runner tests: 70, including 31 mocked preflight/parity failure-path checks; saved-ledger verifier: 120. No later prices needed for synthetic tests.

```bash
.venv/bin/python scripts/verify_hourly_impulse_launch_v11.py
```

Actual saved-ledger result: **passed**; 49 output hashes and 15 source identities at the original builder commit checked, including commit-before-run chronology. Formulas/risk/cost/clocks checked for both 713-trade arms. Case251, control462, matched154; timeout case127/control142. Recovered all original merged fields, including candidate-only unsuffixed launch diagnostics. Checked 48 monthly case rows and three untrimmed paired distributions.

Independently reconstructed D and serial D: `+0.8532657675434971bp`, 80 improved / 47 worsened / 124 unchanged. I: `−0.5982561491103253bp`, 154 known / 97 unknown, 61 improved / 67 worsened / 26 unchanged among known groups.

The first verifier version failed on actual mechanics schema because its synthetic fixture incorrectly suffixed candidate-only columns. Correction derives suffixes from the two authoritative saved schemas and rejects collisions/missing columns/nanosecond time changes. All original fields remain checked. This was an audit implementation defect, not a new strategy run or altered result. See `docs/learnings/merged-ledger-suffixes-follow-shared-schema-not-all-columns.md`.

Limits: verifier sets `raw_replay=false`, `inferential_p_recomputed=false`. It independently checks saved-ledger consistency; it does not independently reconstruct the entire raw-price path, prove price completeness, or reproduce bootstrap/sign-flip inference in a second implementation. Current runner tests were extended after the first replay; original source hashes are verified via `git show 33f31f0`, not falsely compared with later test revisions.

## Financial reconciliation

All-case net mean −14.30663→−13.45337bp, PF .62471→.60070, wins62→51. Four half-years remain negative. D95%CI `[−4.05610,+5.08846]bp`, p=.3640; no reliable improvement. Matched154 case −11.81482→−13.14729bp, control −20.96135→−21.69557bp, conditional excess +9.14653→+8.54828bp. I95%CI `[−7.54919,+5.55992]bp`, p=.5682. Fixed61.35% coverage remains below90%; no gate was lowered.

15 win-to-loss versus4 loss-to-win, 185 loss-to-loss and47 win-to-win. All127 changed exits are launch timeouts;14 hard stops unchanged. Final200 losses comprise163 nonpositive gross exits and37 small gross profits consumed by cost. This terminal classification does not determine whether a trade had large intratrade profit. Hierarchical10 giveback labels are not the count of every loss with MFE≥1R.

Reporting review corrected control-risk wording and the distinction between terminal gross loss and path-wise MFE. Report, canonical artifact and HTML were regenerated consistently; saved strategy results remained unchanged. No claim that historical examples provide live entry filters.

## Report and notebook delivery

Primary HTML: `analysis/html/p1_btcusdtp_hourly_launch_v11_20260906.html`.
Source: `analysis/p1_btcusdtp_hourly_launch_v11_20260906.md`.
Canonical artifact: this experiment's `artifact.json`.

Official portable pipeline receipt after prose review:

```json
{"ok":true,"stages":{"validation":"passed","package":"passed","verification":"structural_only"},"browserWarning":{"code":"browser_unavailable"},"counts":{"blocks":16,"charts":1,"html":0,"metrics":0,"tables":0},"sourceDialog":"not_verified","sourceInteraction":"not_verified","viewports":[]}
```

One actual-SQLite fixed-bin chart counts all251 paired deltas, including zero and open-ended tails; not density, no trimmed observations. Financial tables remain readable Markdown inside canonical report blocks, hence manifest table count0. Structural/payload/semantic fallback validation passed. No installed compatible Chromium headless shell: mobile, visual, interaction and source-dialog behavior **not verified**. No browser installed.

Supporting notebook: `analysis/output/btcusdtp_hourly_launch_v11_20260906/launch_audit.ipynb`.
11 cells, four code cells executed in plain Python top-to-bottom. Independently checked251 identities/deltas,127 timeouts and502 closed-case cost formulas. Minimum nbformat4.5 structure and code compilation passed. No Jupyter kernel execution or full nbformat schema validation: nbformat, nbclient, ipykernel unavailable; no dependencies installed. Controls, p-values and raw path are outside this notebook's scope.

## Reproduction commands

Run from repository root using the existing pinned `.venv` (Python3.9.6, pandas2.3.3, NumPy2.0.2, SciPy1.13.1). For original first execution, research builder/config/plan must already exist in git. These commands refuse to overwrite existing research/facts/notebook/artifact outputs; preserve originals and allocate an explicitly named new attempt for any replay. Do not delete evidence or rerun a price study merely to refresh prose.

```bash
.venv/bin/python -m yoyo.evaluation.hourly_impulse_launch_research
.venv/bin/python scripts/verify_hourly_impulse_launch_v11.py
.venv/bin/python -m yoyo.evaluation.hourly_impulse_launch_facts
.venv/bin/python -m yoyo.evaluation.hourly_impulse_launch_notebook --output analysis/output/btcusdtp_hourly_launch_v11_20260906/launch_audit.ipynb --check
.venv/bin/python scripts/md_to_html.py analysis/p1_btcusdtp_hourly_launch_v11_20260906.md --out-dir analysis/html
.venv/bin/python -m yoyo.evaluation.hourly_impulse_launch_report --markdown analysis/p1_btcusdtp_hourly_launch_v11_20260906.md --summary experiments/active/exp-btcusdtp-1h-launch-deadline-preholdout-20260906-v11/results/summary.json --case-delta experiments/active/exp-btcusdtp-1h-launch-deadline-preholdout-20260906-v11/results/case_delta.csv --output experiments/active/exp-btcusdtp-1h-launch-deadline-preholdout-20260906-v11/artifact.json
node /Users/zhangzc/.codex/plugins/cache/openai-curated-remote/data-analytics/0.2.10-13ceeea1f599/skills/build-report/scripts/deliver_portable_artifact.mjs --input experiments/active/exp-btcusdtp-1h-launch-deadline-preholdout-20260906-v11/artifact.json --output analysis/html/p1_btcusdtp_hourly_launch_v11_20260906.html
```

After prose review the canonical artifact was rebuilt with the same deterministic builder and existing snapshot timestamp, applied as a reviewed file patch before first delivery. Artifact equality and packaging were checked again. No underlying snapshot distribution or study result changed.

## Next action and unresolved evidence

Do not promote or tune the rejected60min threshold against retrospective winners. A bounded future hypothesis is structural loss of the frozen K1 MA boundary, not slow progress alone; first deduplicate against existing colour, alignment and source-zone mechanisms, then preregister a single causal change. It has not been registered or run. Reused development, insufficient matching support and absence of fresh independent validation remain unresolved. Stable profitability is not established.
