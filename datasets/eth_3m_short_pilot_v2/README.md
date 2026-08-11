# ETH 3m short-start pilot v2

Image-level causal-tip classification dataset. `train/` and `val/` use Ultralytics-compatible class folders (`no_start`, `short_start`).

Current build: 137 labeled images (30 short_start / 107 no_start), grouped into 29 independent positive events.

Only `confirmed_current_tip` and `owner_no_tip_negative` are training labels. T-1/T+1/T+2/T+3/original-v10 candidates have blank targets in `weak_or_review_manifest.csv` and live only under `weak_or_review/`; the tip/tip-1/tip-2 detection tolerance is not a signal-lifetime rule.

See `owner_confirmation_receipt.json` for the batch-confirmation hashes, `build_meta.json` for the label contract, and `smoke_manifest.csv` for the sealed unlabeled continuous development replay.

This is a diagnostic pilot. It is not a formal gold set and must not be promoted or evaluated on holdout without owner approval.
