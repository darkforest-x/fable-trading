# Frozen forward refresh evidence — 2026-07-12 04:30 +08:00

- command chain: `python3 -m src.data.update_okx` → `PYTHONPATH=. python3 scripts/forward_track_shadows.py` → `PYTHONPATH=. python3 scripts/daily_digest.py --dry-run` → `bash scripts/deploy_vps.sh`
- data refresh: 7,124 new 15m bars across 456 symbols
- scope: frozen q90 champion and H1 shadow only; holdout false; ACTIVE, threshold, cost, candidates and exits unchanged
- main ledger: 67 total / 56 closed / 11 open / 0 duplicate; uniform semantics 65 total / 54 closed
- H1 ledger: 67 total / 59 closed / 8 open / 0 duplicate; uniform semantics 65 total / 57 closed
- fixed 0.20% main: net sum `+7.990%`, net/trade `+0.1480%`, PF `1.4330`, win `51.85%`
- fixed 0.20% H1: net sum `+5.043%`, net/trade `+0.0885%`, PF `1.3032`, win `59.65%`
- main SHA-256: `f927a82424d0732d09db23cd22d6666104713e6a789cbbdf8e02187dc09e8e5b`
- H1 SHA-256: `5053e57d8563ff2440ffcc5485fa9c3a45310f8d391c51a9bb358529ddca8bcf`
- digest: 0 anomalies; Telegram skipped
- VPS: active, HTTP 200, `ENABLE_JOB_EXECUTOR=0`, ops env mode 600

Both uniform books remain positive at fixed cost, but 54/57 closed rows remain below the pre-registered 100-row confirmation gate. No profitability acceptance or promotion is made.
