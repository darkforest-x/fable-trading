# YOLO Label/Training Task Pack for Other Models

## Hard constraints

- Do not touch holdout.
- Do not change `src/detection/auto_label.py` thresholds without owner approval.
- Do not enable YOLO augmentations: flip/mosaic/mixup/hsv direction-breaking switches stay off.
- Do not claim mAP improvement from relaxed IoU/conf.
- Output findings as files under `output/offline_tasks/` or `output/label_audits/`; do not edit tracked code.

## Current local state

- Main audit page: `http://127.0.0.1:8643/label_audit.html`
- Extra audit pages: `output/label_audits/label_audit_seed_*.html`
- Round-1 sample list: `analysis/p2a_label_audit_round1.md`
- YOLO full-s result: mAP50 0.8569, P 0.8003, R 0.7112; formal target mAP50 >= 0.90.
- Known issue from owner: many boxes are inaccurate.

## Task A: visual label audit

Open every audit page and produce:

`output/offline_tasks/yolo_label_audit_findings.csv`

Columns:

```csv
page_seed,image,split,current_label,error_type,reason,suggested_action
```

Allowed `error_type` values:

- `normal`
- `missing_label`
- `false_label`
- `box_too_wide`
- `box_too_narrow`
- `box_split_merge_mismatch`
- `unclear`

Suggested actions should be descriptive, not parameter changes unless obvious.

## Task B: parameter-change proposal

After Task A only, group findings into root causes and write:

`output/offline_tasks/yolo_label_audit_recommendations.md`

Required sections:

1. Summary counts by error type.
2. Top 3 root causes.
3. Proposed single-variable experiments.
4. Which proposals require owner approval.
5. Which proposal should run first and why.

Do not edit code.

## Task C: SAHI/FiftyOne feasibility

Only if dependencies are available in an isolated environment. Do not install into `.venv`
without owner approval.

Write:

`output/offline_tasks/yolo_tooling_feasibility.md`

Answer:

- Can SAHI evaluate current weights without changing the training set?
- Can FiftyOne import `datasets/dense_15m_full` and show label issues?
- What exact command sequence would run offline?
- What new dependency/environment is required?

## Task D: expanded SWAP universe follow-up

After `fable_expand_swap_15m_fixed_*` finishes, inspect:

- `output/offline_tasks/okx_swap_universe.csv`
- `data/kline_fetched/okx_*_USDT_SWAP_15m_*.csv`

Write:

`output/offline_tasks/swap_universe_expansion_report.md`

Include:

- live OKX USDT-SWAP count
- fetched count
- usable count with enough history
- excluded bases from loader blocklist
- symbols too new or too short
- recommendation: include all / liquid subset / filtered subset
