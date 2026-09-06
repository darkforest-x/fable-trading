# Matching coverage can select a different entry population

Date:2026-09-06. Context: original BTC hourly MA-cross K1 family, planning after
the rejected V9 exit-cadence mechanism test. Saved pre-entry records only; no
new raw-price read, outcome computation or strategy selection.

## Problem and evidence

V4 retained251 original mothers but matched only154 to three non-reused exact
controls.94 had insufficient controls and3 lacked a causal ATR volatility bucket.
Among the94 shortages, remaining supply0/1/2 occurred23/38/33 times. Missingness
was not exchangeable: direction-adjusted hourly slope>0 had103matched/6short/
2missing out of111; slope<=0 had51matched/88short/1missing out of140.

Thus the154 matched subset is enriched for an already-known entry background.
Its conditional excess cannot be promoted to the whole251-event population.
This does not establish whether matching caused the shortage through sparse
cells, crossing exclusions or previous assignments: the full matching frame and
failed-row keys were not persisted. Cumulative pool size before exact keys is
not the contemporaneous cell supply relevant to a particular mother.

## Approach

Diagnose support before testing another profit filter. Freeze all original IDs,
exact month/time/volatility/colour/slope keys, three controls and no reuse. Persist
every mother key, support flag, eligible edge and supply/exclusion/used count.
Compute the maximum attainable complete-mother assignment under the SAME
constraints, independently of returns and random seeds.90% of251 requires226
matched mothers. If even the strict upper bound is lower, do not weaken the
control contract to get a passing coverage number.

Report whole-population paired strategy changes separately from supported-subset
conditional excess. Neither creates random treatment assignment; a new target
population or future data needs a separately frozen design. Filtering by match
availability silently changes the estimand and can inherit the slope filter.

## Reproduction sources

- `experiments/active/exp-btcusdtp-1h-first-k2-preholdout-20260906-v4/results/assignments.csv`
- Same directory: `original_mothers.csv.gz`, joined one-to-one on event_id;
  crosstab ma_slope_atr>0 against match_status reproduces counts above.
- `analysis/output/btcusdtp_1h_impulse_ltf_exit_20260906_v1/exit_controls/5m_native40_pairs.csv`
  records no_causal_vol_bucket for all three same unsupported IDs.
- `yoyo/evaluation/hourly_impulse_k2_matching.py`: build_matching_frame and
  assign_controls define support, exclusions, exact keys and no reuse.

Independent reviewer and root recomputation agree. Full support reconstruction
and maximum assignment have NOT run; this note records a representativeness
warning and next audit design, not an improved or profitable trading system.
