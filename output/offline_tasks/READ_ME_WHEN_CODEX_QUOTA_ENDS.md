# Offline Tasks Status Guide

## Check running tasks

```bash
screen -ls
```

If a task name disappears from `screen -ls`, it has ended. Read its log/output below.

## Running / service screens

- `fable_expand_swap_15m_fixed_20260709_220053`
  - Purpose: fetch missing OKX USDT-SWAP 15m histories.
  - Log: `output/offline_tasks/expand_swap_15m_fixed_20260709_220053.log`
  - Done marker in log: `expand swap fixed finished`

- `fable_yolo_tools_20260709_220542`
  - Purpose: install SAHI/FiftyOne into `.venv_yolo_tools`, run read-only tooling eval.
  - Log: `output/offline_tasks/yolo_tools_20260709_220542.log`
  - Main output: `output/offline_tasks/yolo_tooling_eval_report.json`
  - Done marker in log: `yolo tools task finished`

- `fable_post_expand_data_audit_20260709_220937`
  - Purpose: wait for swap expansion, then audit kline gaps/zero volume/bad OHLC.
  - Log: `output/offline_tasks/post_expand_data_audit_20260709_220937.log`
  - Outputs: `output/offline_tasks/data_audit_after_expand_summary.json`, `output/offline_tasks/data_audit_after_expand.csv`

- `fable_post_yolo_tools_summary_fixed2_20260709_222852`
  - Purpose: wait for YOLO tooling eval finished marker, then write a short markdown summary.
  - Log: `output/offline_tasks/post_yolo_tools_summary_fixed2_20260709_222852.log`
  - Output: `output/offline_tasks/yolo_tooling_eval_summary.md`

- `fable_final_summary_fixed2_20260709_222852`
  - Purpose: wait for SWAP expansion, YOLO tooling eval, post-expand audit, and post-yolo summary.
  - Log: `output/offline_tasks/final_summary_fixed2_20260709_222852.log`
  - Output: `output/offline_tasks/FINAL_OFFLINE_SUMMARY_CORRECTED.md`

- `fable_audit_server_8643`
  - Purpose: serve audit page at http://127.0.0.1:8643/label_audit.html
  - Log: `output/offline_tasks/audit_server_8643.log`

## Commands to inspect results quickly

```bash
screen -ls

tail -40 output/offline_tasks/expand_swap_15m_fixed_20260709_220053.log
tail -40 output/offline_tasks/yolo_tools_20260709_220542.log
cat output/offline_tasks/data_audit_after_expand_summary.json 2>/dev/null || true
cat output/offline_tasks/yolo_tooling_eval_summary.md 2>/dev/null || true
cat output/offline_tasks/yolo_sahi_direct_comparison_20260710.md 2>/dev/null || true
find data/kline_fetched -maxdepth 1 -type f -name 'okx_*_USDT_SWAP_15m_*.csv' | wc -l
```

## Tasks for other models

Use: `output/offline_tasks/yolo_other_model_task_pack.md`

Expected outputs from other model:

- `output/offline_tasks/yolo_label_audit_findings.csv`
- `output/offline_tasks/yolo_label_audit_recommendations.md`

Hard rules: no code edits, no training, no threshold changes, no holdout.

## Latest status update: 2026-07-10 00:53 CST

### Offline screens

- `fable_expand_swap_15m_fixed_20260709_220053` **still running**. Fetched SWAP 15m files ~190 (from 54). Missing plan was 347; batch ~4 of 40-symbol workers in progress.
- YOLO tooling **done** (direct > SAHI on 80-image sample).
- `fable_post_expand_data_audit_*` / `fable_final_summary_*` still waiting on expand finished marker.
- `fable_audit_server_8643` up (`/label_audit.html` → 200).

### Other-model task pack progress (Grok 07-10)

| Task | Status | Output |
|---|---|---|
| A visual label audit | **Done (agent proxy)** | `yolo_label_audit_findings.csv` |
| B recommendations | **Done** | `yolo_label_audit_recommendations.md` |
| C SAHI/FiftyOne feasibility | **Done** | `yolo_tooling_feasibility.md` |
| D SWAP expansion report | **INTERIM** | `swap_universe_expansion_report.md` |
| Project problems / YOLO why | **Done** | `PROJECT_PROBLEMS_AND_YOLO_WHY.md` |

Label audit extract: `output/offline_tasks/label_audit_extract/` (18 jpgs).

### Hard rules still hold

- Do **not** edit `auto_label.py` until owner confirms findings (agent audit ≠ final owner sign-off).
- Do **not** retrain YOLO / promote SAHI / touch holdout.
- Expand watchers: no action unless traceback at end of expand log.
