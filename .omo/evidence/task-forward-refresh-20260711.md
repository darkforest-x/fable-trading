# Frozen forward refresh evidence — 2026-07-11

- command chain: `python3 -m src.data.update_okx` → `PYTHONPATH=. python3 scripts/forward_track_shadows.py` → `PYTHONPATH=. python3 scripts/daily_digest.py --dry-run` → `bash scripts/deploy_vps.sh`
- scope: frozen SMA/EMA 20/60/120 q90 champion and H1 shadow only; holdout false; ACTIVE, threshold, cost and exits unchanged
- final data refresh: 840 new bars across 456 symbols after removing a concurrent orphan updater
- main ledger: 57 total / 39 closed / 18 open / 0 duplicate; SHA-256 `419edf3a0658e4dd02eebc7c44e1b873b0a6baf92c0d62f0972b0440f0ba89dc`
- H1 ledger: 57 total / 40 closed / 17 open / 0 duplicate; SHA-256 `68a700d07505523fed7bb692c4dc9ff3c0791ba9208f6ddeaa313547f2557a09`
- explainability replay: 57/57 scores matched; 55 rows use current candidate semantics and 2 preserved rows are legacy semantics
- excluded legacy rows: `ONT_USDT_SWAP 2026-07-10 13:00 UTC`, `TSLA_USDT_SWAP 2026-07-10 11:30 UTC`

## Uniform candidate-semantics economics

| Book | Closed | Cost | Net sum | Net/trade | PF | Win rate |
|---|---:|---:|---:|---:|---:|---:|
| q90 main | 37 | 0.06% | +5.149% | +0.1392% | 1.3976 | 43.24% |
| q90 main | 37 | 0.20% | -0.031% | -0.0008% | 0.9980 | 43.24% |
| H1 shadow | 38 | 0.06% | -0.743% | -0.0195% | 0.9409 | 47.37% |
| H1 shadow | 38 | 0.20% | -6.063% | -0.1595% | 0.6056 | 44.74% |

The main book is positive only under the idealized 0.06% maker assumption and is essentially flat/slightly negative at the project's fixed 0.20% cost. With only 37 uniform closed rows, this is not profitability evidence.

## Operational acceptance

- digest dry-run: 0 anomalies; Telegram skipped
- VPS: `fable-dashboard` active; local health HTTP 200
- executor: `ENABLE_JOB_EXECUTOR=0`
- ops secret file: present, mode 600; token not printed
- public board: `http://103.214.174.58:8642`

The first deploy observed source files disappearing during rsync. A completed q80 `screen` socket had vanished, but its process group still ran `update_okx` and rotated row-count-suffixed CSV files. After terminating that orphan process group, rerunning a complete update, and deploying again, the same deployment completed successfully. No source, strategy or frozen parameter was changed.
