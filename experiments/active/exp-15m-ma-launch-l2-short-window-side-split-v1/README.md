# 15m MA launch L2 short-window side split v1

Owner corrected the L2 contract: this layer must judge the same 18/19-bar
1280×742 input seen by L1, not a separate 168-bar context.  The experiment
reclusters the frozen 25,911 pre-holdout L1 candidate boxes independently by
LONG/SHORT, verifies every representative input pixel hash, derives box-aware
visual-coordinate features, and fits independent return regressors.

“Same input” means the values actually visible in that frozen L1 image: 18/19
OHLC bars, the six SMA/EMA 20/60/120 lines, and the current raw box/confidence.
The visible MA values causally retain earlier-close state by definition; no
earlier raw bar or separate 48/96/168-bar statistic is exposed to L2.  The
matched random control uses causal ATR only for evaluation, never as a model
feature, and keeps only events with all eight non-overlapping assignments.

No holdout, promotion, deployment, ACTIVE/frozen/forward mutation, Telegram,
or order action is authorized.  The old global-context and side-split reports
remain immutable historical negative results.
