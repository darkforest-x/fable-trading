# Floating profit must stop at the execution event

- **Problem**: Counting trades that first floated a profit and later stopped out requires a path audit, not merely each trade interval's high and low.
- **Dead end**: Including the complete exit candle can count a short's low after an earlier O-H stop, or a long's high after a gap-down stop. This would create profits the position never experienced. This was rejected during implementation, not treated as a historical finding.
- **Effective path**: Reuse the immutable framework exit. Scan complete preceding candles, then truncate the exit candle at the native O-H-L-C segment and fill. A gap exit ends immediately; a fixed target receives no excursion credit beyond its actual limit fill. Legacy frozen fills infer the segment from the same declared ordering. Preserve the preceding-candle MFE separately as a conservative lower bound.
- **General rule**: An excursion is a post-trade diagnostic, never a replacement execution engine. Its time window ends at the actual exit event, and OHLC ordering uncertainty must remain visible. Report planned reward/risk, realized payoff ratio and profit factor separately.
- **Scope**: `yoyo/evaluation/monday_body_hold.py`, eight focused body/causality/path fixtures, and `exp-monday-1h-body-hold-vectorbt-20261024-v1`. No trade barriers, fees or live behavior changed by the diagnostic.
