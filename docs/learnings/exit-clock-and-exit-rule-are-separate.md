# A faster exit clock does not imply a longer profitable hold

- **Problem**: The first confirmed15m pivot appeared to exit too early; the owner proposed5m management and later a key-level half exit with90minute cap.
- **Dead end**: Treating5m as automatically better at capturing a trend. On the same15BTC entries, the first5m pivot closed11earlier and changed net+17.24R to-11.69R. This changes detection speed, not the decision to exit at the first pivot.
- **Effective path**: Freeze entries, exact stops, cost and paired controls. Compare clock alone, then change the exit rule. A5m opposite-pivot stop ratchet gave+14.49R;90minute cap+14.10R;adding first-key half realization+6.49R. Keep actual confirmation/fill times, original-risk weighting, and final incomplete positions separate.
- **General rule**: Separate signal clock, exit clock and exit action. Do not infer maximum-profit capture from faster candles or earlier partial profit. Compare complete cohorts; here every positive variant still loses after removing its largest winner.
- **Related**: `yoyo/evaluation/range_exit5m.py`, `range_key_time_exit.py`, and `yoyo/layers/l3_backtest/nautilus_independent.py`. Structural stops use post-entry2/2 pivots, tighten only, and become active after confirmation; partial legs conserve original shares and20bp fees.
