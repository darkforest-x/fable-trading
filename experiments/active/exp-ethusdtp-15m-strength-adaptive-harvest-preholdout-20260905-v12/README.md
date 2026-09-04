# ETHUSDT.P 15m strength-adaptive harvest V12

The V11 two-candle cooling trigger stays frozen. A release uses the normal
20/10/5/5% schedule while the observed move has earned only +2/+4 ATR slots.
Once +8 ATR has already occurred, its next release is discounted by the
selected multiplier because the trade has demonstrated super-trend strength.

The decision is causal at the completed trigger close. Every fill only reduces
size and never changes the stop. The remainder keeps the original SMA60/ATR
exit. Repository holdout is physically excluded.
