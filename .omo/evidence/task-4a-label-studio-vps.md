# Task 4A — Label Studio VPS deploy evidence

## Phase A: deployment scripts only (no SSH)

### Hypothesis
Secure bootstrap scripts + unit + gitignore + static tests can enforce the
safety contract before any remote install.

### Pass criteria (scripts)
- Django-valid email (`example.com`)
- Password never printed/argv; trap cleanup on EXIT/INT/TERM
- Signup disabled; pack=80; MemoryMax; dashboard ENABLE_JOB_EXECUTOR=0
- Telegram paste unused; secrets gitignored
- bash -n + pytest + git diff --check + secret scan green
- one pushed commit; worktree clean except `.omo/`, `data`, untracked packs

### Result: PASS
- Commit: `f5d2d55` *Add secure Label Studio VPS deployment scripts*
- Branch: `codex/grok-2day` (pushed; HEAD == origin)
- Files: `.gitignore`, `scripts/deploy_label_studio_vps.sh`,
  `scripts/label_studio_vps.service`, `tests/test_label_studio_vps_deploy.py`
- `bash -n scripts/deploy_label_studio_vps.sh` OK
- `python3 -m pytest tests/test_label_studio_vps_deploy.py` → **10 passed**
- `git diff --check` clean
- Secret scan: no bot-token pattern; password only via stdin pipe / access note
- Default email: `fable-review@example.com` (not `.local`)
- Local pack staged: 80 png under `output/label_studio/pack_80/` (untracked)

### Baseline comparison
- Pre: partial untracked scripts / incomplete contract
- Post: tracked safety-tested deploy surface; no remote service yet

### Bottleneck / next hypothesis
Scripts alone do not prove VPS health. Next atomic: run deploy once over SSH
and verify systemd + dashboard + 80-image pack without browser QA.

## Phase B: remote deploy (pending)
See NEXT_TASK.md.
