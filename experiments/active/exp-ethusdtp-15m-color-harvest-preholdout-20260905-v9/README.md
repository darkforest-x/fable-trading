# ETHUSDT.P 15m candle-color harvest V9

Profit milestones at +2/+4/+8/+12 signal ATR earn release slots. A long waits
for a completed red candle; a short waits for a completed green candle. Each
adverse-color candle releases at most one earned slot at the next open, so a
strong same-color impulse is never mechanically harvested.

Every partial fill only reduces size. It never changes the stop. The residual
position keeps the original SMA60/ATR exit. Only per-stage fraction is selected,
and repository holdout is physically excluded.
