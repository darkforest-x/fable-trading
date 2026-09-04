# ETHUSDT.P 15m hybrid profit harvest V8

The +2 and +4 signal-ATR stages bank immediately to protect early profit. The
+8 and +12 stages only earn release slots; they are sold one at a time after
completed adverse-side EMA30 closes. This avoids harvesting a strong trend
while it is still accelerating.

Every fill only reduces size and never changes the stop. The residual position
keeps the original SMA60/ATR exit. Only the fraction per stage is selected, and
repository holdout is physically excluded.
