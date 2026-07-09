# Morning brief — overnight merge helper

**Date:** YYYY-MM-DD  
**Source plan:** `output/offline_tasks/OVERNIGHT_AUTONOMOUS.md`  
**Status pulse:** `output/offline_tasks/OVERNIGHT_STATUS.md` (heartbeat every ~30m)

Fill this at first light. Do **not** expand scope: no holdout, no YOLO flip/mosaic/hsv, no free shell on VPS, no secrets in git. Deploy only if milestone landed **and** tests green **and** change is already approved (prefer docs-only / no deploy).

---

## 1. Quick pulse (2 min)

```bash
cat output/offline_tasks/OVERNIGHT_STATUS.md
screen -ls
git log --oneline -15
PYTHONPATH=. python3 -m pytest tests -q
```

| Check | Expected | Actual (fill) |
|-------|----------|---------------|
| Heartbeat file fresh | `OVERNIGHT_STATUS.md` updated within ~30–60m of sleep end | |
| `screen -ls` | Overnight named sessions either still running or cleanly gone | |
| `git log -15` | Phase 0+1 on main; Phase 2 job runner only if merge landed overnight | |
| Pytest | All green (`56 passed` baseline 2026-07-10; recount if tests added) | |

---

## 2. Expected morning state (from overnight tracks)

Map each overnight track to what “done” looks like. Mark status: **done / partial / failed / still running**.

| Track | Mechanism | Expected by morning | Status | Evidence path |
|-------|-----------|---------------------|--------|---------------|
| P2.5 Phase 2 job runner | worktree subagent | Whitelist job runner + 任务 tab + tests merged (or clean PR/branch ready) | | `git log`, `src/webapp/*`, `tests/test_ops*` |
| SWAP expand FINAL | monitor subagent | Expand finished; report written | | expand log / `FINAL_*` / `data/kline_fetched` SWAP count |
| H1 shadow + forward docs | subagent | Plans + forward smoke docs landed | | `output/offline_tasks/*`, forward docs |
| YOLO E2.1 retrain | screen `fable_yolo_e21_train` | yolo11s trained on relabeled `dense_15m_full` | | `logs/yolo_e21_train_*.log`, `runs/detect/` |
| Val preds for FO | screen `fable_yolo_preds_val` | `preds_val_conf30` for mistakenness | | dataset preds dir / preds log |
| Status heartbeat | screen `fable_overnight_status` | `OVERNIGHT_STATUS.md` kept current | | this file’s pulse section |

### Baseline already on `main` (pre-overnight)

Do not re-verify as “overnight success”; treat as known good starting point:

- **P2.5 Phase 0+1** (`b35a03b` and follow-ups): ops auth, experiment registry, agenda tabs — read-only, **no job runner**.
- Review tooling: FiftyOne / Label Studio (`990fa67`).
- E2 core-trim: `MAX_DENSE_BARS` 24 → 12 (`7c935d7`).

### Guardrails still enforced

- No holdout evaluation  
- No free shell job types on VPS executor  
- No secrets written to git  
- No YOLO flip/mosaic/hsv direction-breaking aug  
- Deploy only when milestone lands and script already approved  

---

## 3. Morning checklist (execute in order)

1. **Status** — `cat output/offline_tasks/OVERNIGHT_STATUS.md`
2. **YOLO** — train log metrics / any write under `analysis/` or `runs/detect/`
3. **Git** — `git log --oneline -15` for Phase 2 (or other) merges; note unmerged worktrees
4. **Deploy?** — only if new dashboard features merged **and** tests green **and** owner-approved path; otherwise skip
5. **UI** — open E2.1 compare (if relabel/retrain ready) + **实验** / **议程** tabs (Phase 0+1)
6. **Tests** — re-run `PYTHONPATH=. python3 -m pytest tests -q` after any merge before deploy

---

## 4. CI / test gate (merge helper)

Before any non-docs merge or VPS deploy:

```bash
cd /Users/zhangzc/fable-trading   # or repo root
PYTHONPATH=. python3 -m pytest tests -q
```

| Item | Notes |
|------|--------|
| Full suite | Must be green. Phase 0+1 regressions live mainly in `tests/test_ops_phase01.py` + dashboard tests. |
| Auto-label constants | Do **not** change pad / dense trim constants unless a test assert is wrong; fix test or call out owner. |
| Warnings | Matplotlib deprecation noise is known; not a gate failure. |
| Isolation | Prefer work on `main` carefully; no holdout, no YOLO train, no VPS deploy unless tests green **and** change is deploy-worthy. |

**Last verified (template seed):** 2026-07-10 — `56 passed` in ~6s on `main` @ `990fa67`.

---

## 5. Decision block (owner / next session)

| Question | Answer |
|----------|--------|
| Anything blocked overnight? | |
| Safe to merge Phase 2 (or other branch)? | yes / no / N/A — reason: |
| Safe to deploy VPS? | yes / no — default **no** if docs-only or incomplete runner |
| YOLO E2.1: keep / compare / discard? | |
| SWAP expand: ready for audit/rebuild? | |
| Top 1–3 tasks for this morning | 1. … 2. … 3. … |

---

## 6. One-line summary

> _Overnight: [all green / partial / failed]. Main advance: [Phase2 / expand / YOLO / docs / none]. Next: […]._
