# V9 delivery receipt

## Result and goal status

Rejected sampled-exit candidate, not a profitable system. Profit goal stays active.
The same286 cases average-17.7878 to-16.4331bp net; D+1.3547bp,p=.2269 and
matched I+.8283bp,p=.3229 both fail.207 cases lose in both arms. Only one of four
half-years is net positive. Both959-intention serial ledgers produce286 trades.
No new2025+ price read, holdout use, TradingView change or live mutation.

The next direction is the original hourly MA-cross entry family's pre-entry
support and persistence. It is not registered or run here; V7--V9 source-zone
entries must not be presented as the exact originally requested system.

## Build chronology and tests

- 12df0a0: study builder/config/plan/L3/synthetic tests before the sole price run.
- 78f7243: fence-aware report adapter and paired-mechanics source identity.
- 046b92a: independent CSV-only verifier plus51 corruption-counterexample tests.
- Final source MD immediately converted via scripts/md_to_html.py; canonical
  report artifact and official portable HTML then regenerated after78f7243.
- Old V1--V7 and V8 source/HTML/canonical artifacts compare byte-identical to998136b.

```bash
.venv/bin/python -m pytest tests/test_hourly_impulse*.py tests/boundaries/test_layer_imports.py tests/causality/test_holdout_boundary_is_single_valued.py -q
.venv/bin/python -m yoyo.evaluation.hourly_impulse_cadence_verify
```

Observed **1342 passed in16.72s**. Actual saved-ledger verifier returned
`status: passed`, checking2270 fills, unchanged entries/risks,286 case deltas,
283 finite matched deltas plus3 unknown, and959 intention deltas with an
independent single-position loop. Independent code, context/provenance and
report reviews found no remaining blocking discrepancy. Details and limits are
in VERIFICATION.md; this is not a second independent raw-price replay.

## Official portable receipt

```json
{"ok":true,"stages":{"validation":"passed","package":"passed","verification":"structural_only"},"browserWarning":{"code":"browser_unavailable"},"counts":{"blocks":15,"charts":1,"html":0,"metrics":0,"tables":0},"sourceDialog":"not_verified","sourceInteraction":"not_verified","viewports":[]}
```

All authored text remains14 native markdown blocks; one native12-bin count chart
contains286 cases,75 positive/92 negative/119 zero/0 unknown. Unequal/open bins
are explicit. Source provenance and canonical payload/semantic fallback passed.
Compatible Chromium headless-shell is unavailable; mobile/desktop/light-dark
visual QA, source menus and interaction are not verified. No browser install or
bespoke automation was used. Native table count0 excludes authored markdown tables.

## Final artifact identities

| Artifact | Bytes | SHA256 |
|---|---:|---|
| config.json | 2349 | efb52888167cde56b8bec745baeb131317ef58bee2267238bba8cb25683027f2 |
| results/summary.json | 31423 | 5d0b9e7dd960f945f8f7c622df99e176c01e8db8ffcfe359fa53cf130e5eb8bd |
| VERIFICATION.md | 7053 | 85326833e206a72cec830227e3e402f0f3e93e128e4bd14e906e60ab8f6ca793 |
| source report MD | 15608 | f2faf9404ed5970c231856869004fc0b8c77d4c47813f8b33caa7e62f1a481da |
| portable HTML | 466649 | 0c2933f2176430f2db5970e5e6f279f8e585f48c9674bb2976bf1a4cf7abaf74 |
| canonical artifact.json | 37464 | def46ad9aa5b2f7d37e866e6841711051435186a3180af6313471d549c1ebe1f |

Owner delivery: analysis/html/p1_btcusdtp_hourly_cadence_v9_20260906.html.
Source report includes full rules, fixed controls, folds, individual examples,
failure decomposition, robustness, caveats and complete reproduction commands.
Training/production eligibility remainsfalse. No guarantee of future returns.
