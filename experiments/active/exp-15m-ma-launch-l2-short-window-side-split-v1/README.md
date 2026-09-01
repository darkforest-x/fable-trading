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

## Final result

The preregistered gate failed on 673 independent final pre-holdout events.
Overall top-decile net return was -4.7 bp after the fixed 20 bp round-trip
cost, permutation p was 0.377162, AUC was 0.4519, and Spearman was -0.1201.
The frozen tune-q90 selection was +4.7 bp overall, but matched controls covered
only 32/61 selected events; the covered subset was -49.0 bp while the unmatched
subset was +63.9 bp.  LONG was negative and nearly degenerate (best iteration
1, ten unique final scores); SHORT was exploratory-positive but nonsignificant
and cannot be promoted after inspecting the same final period.

The experiment is therefore rejected.  Pixel parity and the 15 verification
checks passed, so the result isolates weak economic predictability rather than
a rendering or lineage failure.  See
`analysis/p3_15m_ma_launch_l2_short_window_side_split_20260901.md` and its HTML
counterpart for the full evidence and 40-image actual-input gallery.
