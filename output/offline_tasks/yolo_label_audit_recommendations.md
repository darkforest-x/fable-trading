# YOLO Label Audit Recommendations (Round 1)

Date: 2026-07-10  
Source: `output/offline_tasks/yolo_label_audit_findings.csv`  
Sample: seed `20260709`, 18 images from `dense_15m_full` (P2-11 Round 1)  
Constraint: **no code edits, no training, no holdout, no threshold change without owner approval**

## 1. Summary counts by error type

| error_type | count | share |
|---|---:|---:|
| normal | 12 | 66.7% |
| box_too_wide | 2 | 11.1% |
| box_too_narrow | 2 | 11.1% |
| box_split_merge_mismatch | 1 | 5.6% |
| unclear | 1 | 5.6% |
| missing_label | 0 | 0% |
| false_label | 0 | 0% |
| **total** | **18** | 100% |

Notes:

- All 6 background (0-box) samples are acceptable negatives except one borderline (`LTC_USDT_002660` → unclear).
- Failures cluster on **geometry** (width / edge clip / split-merge), not on “random boxes on empty charts”.
- This is a **18-image audit**, not a full-dataset rate estimate.

## 2. Top 3 root causes

### RC1 — Over-wide segment → bbox mapping (highest impact)

`PAXG_USDT_015960` (and milder `PI_USDT_017060`) show long consolidations painted as one fat rectangle.  
`auto_label.segment_to_bbox` pads x by candle half-width + `x_pad_px=12` and y by `y_pad_frac=0.35`. Long runs that stay under spread thresholds become huge GT boxes. YOLO then learns “big soft rectangles” rather than tight MA cores → **mAP50 hard to push past ~0.86 without cleaner GT**.

### RC2 — Window-edge partial boxes

`ICP_USDT_000760` and `BNB_USDT_011660` have boxes clipped at the left chart edge. The dense episode exists partly outside the rendered window, so GT is incomplete. Model is trained to predict stubs → lower localization quality, higher FP/FN near edges.

### RC3 — Split/merge policy fragmentation

`ALLO_USDT_014860` is one human-dense episode split into two boxes (`MERGE_GAP_BARS=2`, `MIN_DENSE_BARS=5`). Inconsistent object identity confuses NMS matching and makes SAHI’s extra proposals look like “recall” while actually increasing pred count without better IoU50 matches.

## 3. Proposed single-variable experiments

Do **one** change per experiment. Rebuild labels + train only after owner approves the parameter direction. Order by expected value:

| # | Experiment | Variable | How to measure | Expected effect |
|---|---|---|---|---|
| E1 | Tighter x padding | `x_pad_px` 12 → 4 or 6 | re-audit same 18 seeds + new seed; width error rate | Reduce box_too_wide; mAP50 may rise if GT tighter |
| E2 | Shorter y padding | `y_pad_frac` 0.35 → 0.20 | same | Taller boxes shrink; better localization on flat MAs |
| E3 | Edge-partial drop | drop segments with `start==0` or `end==last` (or require ≥N bars fully inside) | edge false-narrow rate on audit | Removes stub GT; cleaner train set |
| E4 | Merge policy | `MERGE_GAP_BARS` 2 → 3 or 4 | split/merge mismatch on multi-box cards | Fewer fragmented objects |
| E5 | Hard-negative mining | add high-conf model FPs on true backgrounds | consistency + val P | Only **after** E1–E3; not first |
| E6 | imgsz 1280 | train imgsz only | mAP vs 960 baseline | Model capacity was already not the bottleneck (0.835→0.857); low priority |
| E7 | SAHI retune | slice/NMS only, **with direct baseline** | matched IoU50 vs direct | Already failed once (75/97 vs 77/97); do not promote |

## 4. Which proposals require owner approval

| Proposal | Needs owner approval? | Why |
|---|---|---|
| E1 `x_pad_px` | **Yes** | Changes GT semantics for every label |
| E2 `y_pad_frac` | **Yes** | Same |
| E3 edge drop rule | **Yes** | Changes which windows contribute positives |
| E4 merge_gap | **Yes** | Changes object identity |
| E5 hard-neg mining | Yes (data composition) | Safe-ish if only adds backgrounds |
| E6 imgsz | Soft (train config) | Allowed by P2-11 ammo list; still single-variable |
| E7 SAHI | Soft | Diagnostic only; already negative once |
| Any conf/IoU redefinition to “pass” 0.90 | **Forbidden** | Explicit project red line |
| flip/mosaic/mixup/hsv | **Forbidden** | Direction-breaking for charts |

## 5. Which proposal should run first and why

**Run E1 first (tighter `x_pad_px`), after owner approval.**

Reasons:

1. Audit evidence is strongest on **width** (`PAXG` is unmistakable).
2. Width errors inflate GT area → model learns soft large objects → caps precision/mAP.
3. Single parameter, reversible, does not require new data fetch.
4. Capacity and SAHI experiments already showed **non-label levers underperform**.
5. Protocol (P2-11): fix labels before model size / hard-neg / resolution.

**Stop condition before any retrain:** re-run `label_audit.py` with seeds `20260709` + one new seed; require width-error examples like PAXG visibly improved and no surge in missing_label/false_label.

## 6. Honest limits

- Agent visual audit is a substitute for owner eyeballs, not a replacement. Owner should still spot-check the 6 non-normal rows.
- Do **not** enter YOLO retrain or change `auto_label.py` until owner confirms findings or amends them.
- Critical path remains **judgment layer + forward ≥100 trades**, not YOLO mAP.
