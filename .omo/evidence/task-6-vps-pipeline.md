# Todo 6B — VPS pipeline surface deploy + browser QA

**Result: PASS**
**When:** 2026-07-10
**Branch:** `codex/grok-2day`
**Baseline:** local atom `ff07060` / evidence `task-6-pipeline-local.md`

## Hypothesis

Deploying current ops console via `scripts/deploy_vps.sh`, forcing
`ENABLE_JOB_EXECUTOR=0`, and enabling `OPS_AUTH_MODE=token` via root-only
`/etc/fable-trading/ops.env` makes public VPS match the local redacted
seven-stage pipeline contract with fail-closed auth.

## Predeclared pass/fail

| Criterion | Result |
|-----------|--------|
| Reversible deploy path used | PASS `bash scripts/deploy_vps.sh` |
| Dashboard active, HTTP 200 | PASS |
| `ENABLE_JOB_EXECUTOR=0` | PASS (unit + response) |
| Auth pipeline GET 7 stages | PASS |
| Anon / wrong token fail closed | PASS 401 / 401 |
| No absolute local path / credential leak | PASS (token value never in JSON; key `OPS_API_TOKEN_configured` redacted) |
| Playwright desktop 1440 + 390px | PASS |
| No console errors / horizontal overflow | PASS |
| Label Studio still up | PASS `fable-label-studio` active; :8082 → 302 |
| Memory headroom | PASS ~1.8 GiB available of 3.9 GiB |
| Secret not committed | PASS ops.env mode 600, untracked |

## Commands (repro shape; no token values)

```bash
# from repo root, branch codex/grok-2day @ 09cb102+
bash scripts/deploy_vps.sh
# on VPS (once): create /etc/fable-trading/ops.env chmod 600 with
#   OPS_AUTH_MODE=token
#   OPS_API_TOKEN=<high-entropy>
#   ENABLE_JOB_EXECUTOR=0
# and EnvironmentFile=-/etc/fable-trading/ops.env in fable-dashboard.service

curl -s -o /dev/null -w "%{http_code}\n" http://103.214.174.58:8642/api/ops/pipeline
# → 401
curl -s -H "Authorization: Bearer <token>" http://103.214.174.58:8642/api/ops/pipeline
# → 200, 7 stages, executor_enabled=false, read_only=true
```

## Real surface snapshot (redacted)

```
anon=401 wrong=401 auth=200
n_stages 7
ids: data, detection_yolo, judgment, backtest, forward, jobs, deploy
read_only true; write_actions []; executor_enabled false
stages: data:ok, detection_yolo:ok, judgment:ok, backtest:label_only,
        forward:ok, jobs:executor_off, deploy:local_ops
```

Browser: `.omo/evidence/task-6-vps-screens/{desktop,mobile390}_{pipeline,data,models}.png`
Machine JSON: `.omo/evidence/task-6-vps-browser.json` (`pass: true`)

## Comparison vs baseline

| | Local (`ff07060`) | VPS (this atom) |
|--|-------------------|-----------------|
| Pipeline API | loopback + token | public :8642 + token |
| Fail closed | tests | live anon/wrong 401 |
| UI 流水线 tab | local | desktop + 390px screenshots |
| Executor | 0 | 0 forced by deploy |
| LS coexist | n/a | active, unchanged |

## Bottleneck / next hypothesis

1. Deploy stage still labels itself `local_ops` even on VPS — cosmetic status
   string; next atom can detect non-loopback bind without leaking paths.
2. `deploy_vps.sh` should re-assert `EnvironmentFile` for ops.env so a unit
   rewrite cannot drop public auth.
3. Owner LS annotation gate remains parallel (0 human annotations).

## Risk / honesty

- Generated VPS `OPS_API_TOKEN` lives only in root-only ops.env; not in git or
  evidence. Owner should rotate if this host is shared.
- `deploy:local_ops` wording is slightly inaccurate on VPS (see bottleneck).
- LS nginx :8081 returned 400 without correct Host; process on :8082 is healthy.
- No holdout reads, no E2.1b start/stop, no live orders, no Telegram.
- Screenshots may show token field dots only (sessionStorage); no plaintext token files.
