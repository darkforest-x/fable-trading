# Pine V9 judgment gate contract

This directory is a fail-closed interface for a future owner-authorized
judgment experiment. It is not a model, score file or execution approval.

`judgment_scores.template.csv` defines the only accepted external score shape.
Every raw guarded V9 candidate in a scored period must have exactly one finite
score, the score must be available no later than the next-bar entry open, and
both model and feature-contract hashes must be fixed. A missing, duplicate or
late score rejects the candidate.

The threshold must be preregistered using an earlier calibration period. Scores
must be joined to `v9_long` / `v9_short` before the stateful replay, because a
rejected entry changes later position and cooldown state. Filtering the
baseline executed-trade CSV after the fact is invalid.

Current status: no labels, no scores, no threshold, no LR/LightGBM training,
no forward collection, no TradingView parity and no production eligibility.
