# Final Offline Summary

Generated after finished markers appeared in all watched logs.

## output/offline_tasks/okx_swap_universe_summary.json
- generated_at_utc: `2026-07-09T13:54:51.887795+00:00`
- okx_live_usdt_swap_count: `401`
- project_15m_fetched_swap_count: `54`
- missing_15m_fetch_count: `347`

## output/offline_tasks/data_audit_after_expand_summary.json
- files: `454`
- swap_15m_files: `399`
- files_with_gaps: `0`
- files_with_bad_ohlc: `0`
- files_with_zero_volume: `210`
- csv: `output/offline_tasks/data_audit_after_expand.csv`

## YOLO tooling summary
# YOLO Tooling Eval Summary

- dataset: `/Users/zhangzc/fable-trading-codex/datasets/dense_15m_full`
- weights: `/Users/zhangzc/fable-trading-codex/runs/detect/runs/detect/dense_15m_full_s/weights/best.pt`

## fiftyone_import_probe
- ok: `True`
- samples: `1255`

## direct_yolo_sample_eval
- ok: `True`
- sample_size: `80`
- gt_boxes: `97`
- pred_boxes: `106`
- matched_iou50: `77`
- recall_like_iou50: `0.7938`
- pred_per_gt: `1.0928`

## sahi_sliced_sample_eval
- ok: `True`
- sample_size: `80`
- gt_boxes: `97`
- pred_boxes: `178`
- matched_iou50: `75`
- recall_like_iou50: `0.7732`
- pred_per_gt: `1.8351`


## Current SWAP 15m file count
- count: `399`

## Files to inspect
- `output/offline_tasks/data_audit_after_expand.csv`
- `output/offline_tasks/yolo_tooling_eval_report.json`
- `output/offline_tasks/yolo_other_model_task_pack.md`
- `output/offline_tasks/fable_gap_and_offline_plan.md`

## Next manual inputs needed
- Owner/model label findings CSV from `output/offline_tasks/yolo_other_model_task_pack.md`.
- Owner approval before any auto_label threshold changes or YOLO retraining.
- Owner approval before adding forward tracking to daily scheduler.