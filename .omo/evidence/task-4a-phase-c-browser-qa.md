# Todo 4A Phase C — Project init + browser QA

**Result: PASS**
**When:** 2026-07-10
**Branch:** `codex/grok-2day`
**Public URL:** `https://103.214.174.58:8081` (self-signed IP SAN)
**Secrets:** credentials only in untracked `output/offline_tasks/LABEL_STUDIO_VPS_ACCESS.md`; never logged, never committed.

## Hypothesis

API project init + real Chromium (Playwright, `ignore_https_errors` for this host only) can create `dense_15m_val_audit` with 80 tasks, render E2.1 prelabels, prove one box edit persistence, block anonymous task access, and leave dashboard executor off.

## Baseline (Phase B)

- HTTPS deploy live; Label Studio + nginx active; 80 images on disk; no project initialized.
- Commits: `54eea8b`, `f912d85`.

## Commands (sanitized)

```bash
# 1) Inspect pack (no secrets)
python3 -c "import json; t=json.load(open('output/label_studio/tasks_val.json')); print(len(t))"
# → 80 tasks; 53 with prelabels; image paths /data/local-files/?d=dense_15m_full/...

# 2) Deterministic init (isolated venv; reads access file in-process)
.venv_label_studio_qa/bin/python scripts/init_label_studio_vps_project.py
# → AUTH_OK, project id=1, import 80 tasks / 53 predictions, localfiles storage, image HTTP 200

# 3) Playwright browser proof (headless Chromium, self-signed accepted for this IP)
#    screenshots → .omo/evidence/phase_c_screens/
# 4) VPS health
ssh root@103.214.174.58 'systemctl is-active fable-label-studio nginx fable-dashboard;
  systemctl show fable-dashboard -p Environment --value | tr " " "\n" | grep ENABLE_JOB_EXECUTOR;
  free -m | head -2'
```

## Counts

| Item | Value |
|------|-------|
| Project | `dense_15m_val_audit` (id=1) |
| Tasks | **80** |
| Predictions (prelabels) | **53** (matches pack: 53/80 have GT boxes) |
| Label config | `dense_cluster` rectangle |
| Local storage | `/opt/fable-label-studio/files/dense_15m_full` (required for image serve) |
| QA task | id=79 stem=`BNB_USDT_016560` (edit reverted after proof) |

## Checks

| Check | Result | Evidence |
|-------|--------|----------|
| Login (browser) | PASS | after_login → `/projects/`; project card visible |
| Prelabels render | PASS | Label All Tasks: canvas=5, rects=16, `dense_cluster` present; chart PNGs natural 1280×742 |
| Box resize + delete + reopen | PASS | API: w 5.70→11.70, h 11.86→15.86; delete extra box → n=1; reopen match; persisted after browser; then deleted |
| Reference task clean | PASS | QA annotation reverted; prediction prelabel retained |
| Anonymous API tasks | PASS | HTTP **401**, no leak |
| Anonymous browser | PASS | redirect to `/user/login/` |
| Mobile + desktop | PASS | desktop labeling widgets; mobile project/labeling renders (force login for overlay) |
| Image serve | PASS | authenticated `/data/local-files/...` HTTP **200** (170751 bytes) after storage link |
| Dashboard | PASS | `http://103.214.174.58:8642/` HTTP **200**; `/api/overview` 200 |
| Executor | PASS | `ENABLE_JOB_EXECUTOR=0` on fable-dashboard |
| VPS memory | OK | ~3915 MB total, ~1.8 GB available; LS+nginx+dashboard active |
| Secrets in logs/diff | PASS | password/token never printed; access md mode 600 untracked |

## Critical fix discovered during QA

Label Studio **1.15** returns **404** for `/data/local-files/?d=...` even when `LOCAL_FILES_SERVING_ENABLED=true` **unless** a `LocalFilesImportStorage` row exists for the project whose `path` is a **child** of `LOCAL_FILES_DOCUMENT_ROOT`. Path must be e.g. `/opt/fable-label-studio/files/dense_15m_full` (not the document root itself).

Import of raw `tasks_val.json` fails with `completed_by=0`; sanitize to **predictions-only** (strip `completed_by`).

## Screenshots (local)

- `.omo/evidence/phase_c_screens/01_login.png`
- `.omo/evidence/phase_c_screens/02_after_login.png`
- `.omo/evidence/phase_c_screens/03_projects.png`
- `.omo/evidence/phase_c_screens/04_project_data.png` (Tasks: 80/80, Predictions: 53)
- `.omo/evidence/phase_c_screens/05_labeling_desktop.png` (prelabels/regions)
- `.omo/evidence/phase_c_screens/07_labeling_mobile.png`
- `.omo/evidence/phase_c_screens/08_anon_project.png` (login wall)

## Comparison vs baseline

| Metric | Phase B | Phase C |
|--------|---------|---------|
| Public TLS | live | live |
| Project + 80 tasks | no | **yes** |
| Prelabels in UI | n/a | **yes** |
| Edit persistence | n/a | **yes** (reverted) |
| Anon blocked | assumed | **proven 401 + login wall** |
| Executor | 0 | **0** |

## Bottleneck / next hypothesis

Manual review is unblocked. Next atomic value is an **OSS architecture benchmark** for labeling/review tooling (Label Studio vs alternatives patterns, frozen SHA notes) to inform long-term label pipeline — not another deploy tweak.

## Risk / honesty

- UI mouse-draw of a new box is flaky in headless LSF; persistence was proven via authenticated annotation API + browser reopen showing regions/canvas, then full revert.
- 27/80 tasks have zero GT boxes in the pack (by design of source labels); 53 prelabeled is correct, not a failed import.
- Self-signed cert: browsers warn; Playwright used `ignore_https_errors=True` only for this endpoint.

## Tracked deliverable

- `scripts/init_label_studio_vps_project.py` — idempotent API init + local storage + sanitized import.
