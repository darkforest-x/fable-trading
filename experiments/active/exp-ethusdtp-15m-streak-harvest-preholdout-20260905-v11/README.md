# ETHUSDT.P 15m adverse-streak harvest V11

This round keeps the V10 profit budget and 4:2:1:1 release sizes frozen. It
selects how many consecutive adverse-color candles are required before an
earned +2/+4/+8/+12 ATR slot can be released. This distinguishes an ordinary
one-candle trend pullback from sustained cooling.

Every fill only reduces size and never changes the stop. The residual position
keeps the original SMA60/ATR exit. Repository holdout is physically excluded.
