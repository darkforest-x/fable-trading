# 15m L2 causal feature addition v1

This experiment asks whether causal OHLCV feature groups add stable economic
ranking value to the frozen 28-feature LONG/SHORT L2 baseline on the current
real-YOLO candidate distribution.

The source candidates, outcomes, dependency representatives, chronological
splits, LightGBM parameters, q90 gate, barriers, cost and matched controls stay
fixed.  Extra features are rebuilt from the frozen pre-holdout snapshot through
each candidate's existing decision bar.  March tune selects one arm per side;
the selection receipt must be committed before April final validation opens.

No holdout read, promotion, deployment, forward mutation, Telegram send or
order placement is authorized.
