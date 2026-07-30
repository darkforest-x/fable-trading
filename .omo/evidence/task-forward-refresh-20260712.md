# Frozen forward refresh evidence — 2026-07-12

- command chain: `python3 -m src.data.update_okx` → `PYTHONPATH=. python3 scripts/forward_track_shadows.py` → `PYTHONPATH=. python3 scripts/daily_digest.py --dry-run` → `bash scripts/deploy_vps.sh`
- scope: frozen SMA/EMA 20/60/120 q90 champion and H1 shadow only; holdout false; ACTIVE, threshold, cost, candidates and exits unchanged
- data refresh: 4,073 new 15m bars across 456 symbols
- main ledger: 59 total / 52 closed / 7 open / 0 duplicate; uniform semantics 57 total / 50 closed
- H1 ledger: 59 total / 58 closed / 1 open / 0 duplicate; uniform semantics 57 total / 56 closed
- main ledger SHA-256: `294f331b8e795c19f4db06072aa717e168ae9215a7d327c2d9813e2849076596`
- H1 ledger SHA-256: `70c64a081e54de8e1cd569c13700640f110c67825643673247bed1d5c4c48849`

## Uniform candidate-semantics economics

| Book | Closed | Cost | Net sum | Net/trade | PF | Win rate |
|---|---:|---:|---:|---:|---:|---:|
| q90 main | 50 | 0.06% | +14.713% | +0.2943% | 2.0256 | 52.00% |
| q90 main | 50 | 0.20% | +7.713% | +0.1543% | 1.4356 | 52.00% |
| H1 shadow | 56 | 0.06% | +11.293% | +0.2017% | 1.8343 | 60.71% |
| H1 shadow | 56 | 0.20% | +3.453% | +0.0617% | 1.2077 | 58.93% |

Both books are positive at fixed 0.20% cost in this short window, but neither has reached the pre-registered 100-uniform-closed gate. This is encouraging forward evidence, not accepted profitability.

## Operational acceptance

- final digest dry-run was executed after both books completed: 0 anomalies; Telegram skipped
- an earlier digest read an in-flight intermediate state and is explicitly discarded
- VPS: `fable-dashboard` active; HTTP 200; `ENABLE_JOB_EXECUTOR=0`; ops env mode 600
- ACTIVE SHA-256: `42df83c98247188873613eec3af04ffd258520a98e8b4b089c5f322b9db8b9c7`
- model SHA-256: `de2c053f26b0d516c9b19bbfb58b9a42b07b23289cf76a373ea4c4c59137a585`
