# YOLO Tooling Feasibility (SAHI / FiftyOne)

Date: 2026-07-10  
Status: **already executed offline** in isolated env `.venv_yolo_tools`  
Primary outputs:

- `output/offline_tasks/yolo_tooling_eval_report.json`
- `output/offline_tasks/yolo_tooling_eval_summary.md`
- `output/offline_tasks/yolo_sahi_direct_comparison_20260710.md`
- `docs/learnings/sahi-needs-direct-baseline.md`

## Answers

### 1. Can SAHI evaluate current weights without changing the training set?

**Yes.** SAHI only changes inference (sliced predict + NMS merge). It does not retrain or rewrite labels.

Result on the same 80-image sample (seed 20260709, conf 0.30, IoU50 1-1 match):

| Mode | GT | Pred | Matched | Recall-like | Pred/GT |
|---|---:|---:|---:|---:|---:|
| Direct YOLO | 97 | 106 | 77 | 0.7938 | 1.09 |
| SAHI sliced | 97 | 178 | 75 | 0.7732 | 1.84 |

**Conclusion:** SAHI did **not** help; more predictions, fewer matches. Keep diagnostic-only. Do not promote into the main detection path.

### 2. Can FiftyOne import `datasets/dense_15m_full` and show label issues?

**Yes (import probe passed).** Report: `fiftyone_import_probe.ok=True`, samples≈1255 in the smoke path used by the eval script.

FiftyOne is useful for browsing hard cases after findings CSV exists; it is **not** a substitute for the project’s self-contained `/label_audit.html` owner review flow.

### 3. Exact offline command sequence (already used)

```bash
cd ~/fable-trading-codex

# isolated env only — do NOT install into .venv without owner approval
python3 -m venv .venv_yolo_tools
. .venv_yolo_tools/bin/activate
pip install ultralytics sahi fiftyone  # versions pinned in the run log

# read-only eval (script lives under offline outputs)
.venv_yolo_tools/bin/python output/offline_tasks/run_yolo_tooling_eval.py

# inspect
cat output/offline_tasks/yolo_tooling_eval_summary.md
cat output/offline_tasks/yolo_sahi_direct_comparison_20260710.md
```

Notes from the rerun: Python 3.9 rejected `dataclass(slots=True)` in an intermediate script; fixed before successful rerun 2026-07-10 00:20 CST.

### 4. New dependency / environment required

| Item | Requirement |
|---|---|
| Env | `.venv_yolo_tools` (isolated) |
| Packages | `ultralytics`, `sahi`, `fiftyone` (+ torch stack) |
| Project `.venv` | **Do not pollute** without owner approval |
| Data | read-only `datasets/dense_15m_full`, weights `runs/detect/.../best.pt` |
| Red lines | no holdout, no train, no auto_label threshold edits |

## Recommendation

1. Tooling feasibility: **done / sufficient**.
2. Next detection work: **label quality (E1 width pad)** after owner approval — not more SAHI.
3. Mainline trading work: continue **forward accumulation**, not YOLO mAP chasing.
