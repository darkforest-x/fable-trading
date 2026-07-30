# ETH 3m Short Pilot v2 Gold Semantics Repair Evidence

## Scenario: syntax importability

- Invocation: `.venv/bin/python -m py_compile scripts/build_eth3m_short_pilot_dataset_v2.py scripts/validate_eth3m_short_pilot_dataset_v2.py tests/test_build_eth3m_short_pilot_dataset_v2.py`
- Binary observable: exit code 0.
- Captured artifact path: scripts compile in place; no output file expected.

## Scenario: focused unit tests

- Invocation: `PYTHONPATH=. .venv/bin/pytest tests/test_build_eth3m_short_pilot_dataset_v2.py`
- Binary observable: exit code 0; `4 passed in 0.24s`.
- Captured artifact path: `tests/test_build_eth3m_short_pilot_dataset_v2.py`.

## Scenario: rebuild dataset with corrected label semantics

- Invocation: `PYTHONPATH=. .venv/bin/python scripts/build_eth3m_short_pilot_dataset_v2.py`
- Binary observable: exit code 0; manifest totals `total=137`, `short_start=30`, `no_start=107`, `independent_positive_events=29`, `weak_or_review_rows=150`; status flags `diagnostic_pilot_only=true`, `formal_gold_dataset=false`, `promotion_eligible=false`, `training_started=false`.
- Captured artifact paths: `datasets/eth_3m_short_pilot_v2/manifest.csv`, `datasets/eth_3m_short_pilot_v2/weak_or_review_manifest.csv`, `datasets/eth_3m_short_pilot_v2/owner_confirmation_receipt.json`, `datasets/eth_3m_short_pilot_v2/build_meta.json`.

## Scenario: independent dataset validation

- Invocation: `PYTHONPATH=. .venv/bin/python scripts/validate_eth3m_short_pilot_dataset_v2.py`
- Binary observable: exit code 0; validation status `passed`; checks include `positive_rows_is_30=true`, `independent_positive_events_is_29=true`, `owner_no_tip_negative_rows_is_107=true`, `weak_targets_blank=true`, `weak_images_outside_class_dirs=true`, `receipt_hashes_valid=true`, `anchor_embargo_at_least_260=true`, `smoke_is_contiguous_3m=true`.
- Captured artifact path: `analysis/output/eth3m_short_pilot_v2_dataset/validation.json`.

## Scenario: product-level count and path smoke

- Invocation: `PYTHONPATH=. .venv/bin/python -c "import json, pandas as pd; from pathlib import Path; d=Path('datasets/eth_3m_short_pilot_v2'); m=pd.read_csv(d/'manifest.csv'); w=pd.read_csv(d/'weak_or_review_manifest.csv', keep_default_na=False); r=json.loads((d/'owner_confirmation_receipt.json').read_text()); v=json.loads(Path('analysis/output/eth3m_short_pilot_v2_dataset/validation.json').read_text()); print({'manifest_rows':len(m),'target_counts':m['target'].value_counts().to_dict(),'sample_kind_counts':m['sample_kind'].value_counts().to_dict(),'positive_events':m.loc[m.target==1,'positive_event_id'].nunique(),'weak_rows':len(w),'weak_targets':sorted(w['target'].unique().tolist()),'weak_top_dirs':sorted({Path(x).parts[0] for x in w['image_rel']}),'receipt_images':len(r['calibration_images']),'validation_status':v['status']})"`
- Binary observable: exit code 0; output `{'manifest_rows': 137, 'target_counts': {0: 107, 1: 30}, 'sample_kind_counts': {'owner_no_tip_negative': 107, 'confirmed_current_tip': 30}, 'positive_events': 29, 'weak_rows': 150, 'weak_targets': [''], 'weak_top_dirs': ['weak_or_review'], 'receipt_images': 30, 'validation_status': 'passed'}`.
- Captured artifact path: `analysis/output/eth3m_short_pilot_v2_dataset/evidence.md`.

## Audit Move

- Old 265-row dataset moved before rebuild to `/private/tmp/eth_3m_short_pilot_v2_265_audit_20260730_002136`.
- Holdout raw rows were not read by the validator; the builder uses the existing pre-holdout prefix loader and records `holdout_consumed_by_build=false`.
