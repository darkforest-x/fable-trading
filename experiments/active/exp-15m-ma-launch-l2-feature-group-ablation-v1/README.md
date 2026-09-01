# 15m L2 feature-group ablation v1

This experiment tests whether the legacy 28-feature LightGBM input is larger
than necessary for the current frozen Grade-A YOLO candidate distribution.
LONG and SHORT are selected independently on the existing tune interval. The
April final-validation interval is opened only after the selection receipt has
been written.

The only experimental variable is the feature-column subset. Candidates,
labels, dependency representatives, chronological splits, LightGBM parameters,
q90 rule, TP/SL/horizon, cost, and matched controls remain frozen. This is
pre-holdout research and cannot promote or deploy a model.

