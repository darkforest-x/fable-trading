# Fable Gap and Offline Plan

Updated: 2026-07-09 22:15 CST

## Already running offline

| Task | Screen | Purpose | Output |
|---|---|---|---|
| Expand SWAP universe | `fable_expand_swap_15m_fixed_20260709_220053` | Fetch missing OKX USDT-SWAP 15m histories | `data/kline_fetched/`, log |
| YOLO tooling | `fable_yolo_tools_20260709_220542` | Install SAHI/FiftyOne in `.venv_yolo_tools`, run read-only smoke eval | `yolo_tooling_eval_report.json` |
| Post-expand data audit | `fable_post_expand_data_audit_20260709_220937` | Wait for SWAP expansion, then audit gaps/zero volume/bad OHLC | `data_audit_after_expand_summary.json`, `.csv` |
| Post-YOLO summary | `fable_post_yolo_tools_summary_20260709_220955` | Wait for tooling eval, then write short summary | `yolo_tooling_eval_summary.md` |
| Audit server | `fable_audit_server_8643` | Serve label audit page | `http://127.0.0.1:8643/label_audit.html` |

## Not done / failed / weak points

| Area | Status | Why it matters | Can be offline? | Current/next action |
|---|---|---|---|---|
| Stage 3 backtest | Failed | PF 1.01 at 0.3% cost, below 1.3 target | Partly | No more holdout tuning; final decision needs forward data |
| Forward tracking scheduled daily | Not done | Final decision depends on new unseen samples | Yes, but owner decision needed | Owner must approve adding to daily scheduler |
| YOLO formal acceptance | Failed | yolo11s mAP50 0.8569 < 0.90 | Yes | Tooling eval running; label audit still needs human/model findings |
| YOLO label quality | Weak | Owner observed many inaccurate boxes | Yes | Other-model task pack prepared; needs findings CSV |
| auto_label rule revision | Not done | Bad labels cap model quality | No, requires owner-approved parameter direction | Wait for findings; do not change thresholds blindly |
| SAHI sliced inference | Not done yet | May improve small dense-cluster recall | Yes | Running via isolated tooling task |
| FiftyOne dataset audit | Not done yet | Can organize label/mistake review | Yes | Running import probe via isolated tooling task |
| Expanded SWAP universe | Incomplete | Project used 54 SWAP, OKX live USDT-SWAP count is 401 | Yes | Fetch task running |
| Data quality audit after expansion | Not done yet | New symbols may be short/bad/noisy | Yes | Waiting for expansion task |
| P2.5 frontend control center | Not done | Frontend is display-first, not a control plane | Mostly code work | Needs auth decision first if execution features go to VPS |
| P3 simulated trading | Not done | No proof of real order/fill behavior | No, needs demo API key and owner approvals | Later stage only |
| Risk controls | Not done | Required before any simulated/live trading | Partly | Can draft config offline; do not execute trades |
| Alerts | Not done | Needed for forward/production monitoring | Partly | Owner must choose channel |

## Good tasks for other models

1. Label audit from generated HTML pages:
   - Input: `/label_audit.html`, `output/label_audits/*.html`
   - Output: `output/offline_tasks/yolo_label_audit_findings.csv`
   - Use task pack: `output/offline_tasks/yolo_other_model_task_pack.md`

2. YOLO root-cause grouping:
   - Input: findings CSV
   - Output: `output/offline_tasks/yolo_label_audit_recommendations.md`
   - No code edits.

3. Expanded SWAP universe review:
   - Wait for expansion + data audit outputs.
   - Output: `output/offline_tasks/swap_universe_expansion_report.md`

4. Frontend control-center spec review:
   - Input: `NEXT_STEPS.md` P2.5
   - Output: a no-code implementation plan with auth-first phases.

## Do not run automatically

- YOLO retraining.
- `auto_label.py` threshold changes.
- Any holdout evaluation.
- Any simulated or real trading.
- Adding forward tracking to daily scheduler without owner approval.
