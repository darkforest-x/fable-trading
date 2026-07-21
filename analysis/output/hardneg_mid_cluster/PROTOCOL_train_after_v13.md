# H-DET-2 hard-neg mid-cluster — inventory only

## What this is
Candidates for **hard negative / empty-label mid windows**: dense GT boxes whose
right edge sits in mid-window (`right ∈ [0.30, 0.90)`), so ≥8 bars of aftermath
remain to the tip. These are the shapes that teach "wait for post-hoc context".

## What this is NOT
- Not a training set yet
- Not empty-label backgrounds copied by pad200 (those have *no* box)
- Not tip-anchored pad200 positives

## Train later (after v13 finishes) — single variable
1. Wait for `models/owner_v13_pad200.pt` (do not kill / steal MPS from v13).
2. Owner approves H-DET-2 experiment.
3. Build a small hard-neg add-on from this inventory (empty labels on the *same*
   mid-aftermath windows, or background class) **without** changing pad200
   positives / thresholds / TP-SL.
4. Finetune one short run from v13 (or v12) with only that add-on.
5. Judge on tip-smoke + mid-box rate — not val mAP alone.

## Reproduce inventory (CPU)
```bash
PYTHONPATH=. .venv/bin/python scripts/build_hardneg_mid_cluster_inventory.py
```
