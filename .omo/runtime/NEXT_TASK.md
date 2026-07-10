# Next Iteration Packet

model: deep

## Priority queue (pick one atom)

### A. Owner review gate (unchanged dependency)

- URL: `https://103.214.174.58:8081` (self-signed)
- Project: `dense_15m_val_audit` (80 tasks; **0 annotations**, 53 prelabels)
- Creds: untracked `output/offline_tasks/LABEL_STUDIO_VPS_ACCESS.md`
- After annotations: re-run
  `.venv_label_studio_qa/bin/python scripts/export_ls_yolo_writeback.py --limit 80`
  Diff source_counts vs baseline MANIFEST `cc462204c4650986…`; propose owner-gated
  promote only. Never overwrite `datasets/dense_15m_full` without approval.

### B. Todo 6 remainder — VPS pipeline deploy (Recommended if no LS annotations)

1. Deploy current branch ops console to VPS via existing deploy script (executor must stay 0).
2. Curl + browser: `/api/ops/pipeline` auth, stages visible, no secrets/abs paths.
3. Desktop + 390px screenshots; evidence `.omo/evidence/task-6-vps-pipeline.md`.
4. Must NOT enable job executor, touch holdout, or promote models.

Pass: VPS matches local redacted contract; executor_off; secrets absent.

### C. Todo 5 remainder — Playwright visual QA

Loopback Playwright: no console errors, 390px no overflow on pipeline/data/models tabs.

### Out of scope

- Holdout, champion promotion, Telegram, job executor on, force-push, main, stop/start E2.1b.
