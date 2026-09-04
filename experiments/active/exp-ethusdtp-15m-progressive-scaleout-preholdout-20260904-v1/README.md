# ETHUSDT.P 15m progressive scale-out

This experiment freezes the V5 K1/K2 trend-regime entries and changes only
position management. Three equal tranches bank profit at causal ATR milestones;
the remainder follows the original SMA60/ATR runner. After a tranche fills, a
monotone floor protects the already banked profit after the fixed 0.2% cost.

The builder reads only the physically bounded prefix ending before 2026-03-01.
It cannot read the repository holdout beginning 2026-05-04, write TradingView,
promote an artifact, modify ACTIVE/forward state, or place an order.
