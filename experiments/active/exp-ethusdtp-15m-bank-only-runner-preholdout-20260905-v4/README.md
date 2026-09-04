# ETHUSDT.P 15m bank-only runner V4

Profit fills only reduce size. They do not move the stop. The remaining size
continues under the original SMA60/ATR runner, which directly implements the
Owner's distinction between gradual take-profit and disguised stop tightening.

Only total bank fraction is selected; repository holdout is physically excluded.
