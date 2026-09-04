# ETHUSDT.P 15m weakness harvest V7

Profit milestones at +2/+4/+8/+12 signal ATR earn release slots but do not sell
while the trend stays strong. Once a completed candle closes on the adverse
side of EMA30, one earned slot is sold at the next open. Further weak closes
release at most one slot each, so profit is harvested progressively.

Every partial fill only reduces size. It never moves the stop. The residual
position keeps the original SMA60/ATR exit. Only per-stage fraction is selected,
and repository holdout is physically excluded.
