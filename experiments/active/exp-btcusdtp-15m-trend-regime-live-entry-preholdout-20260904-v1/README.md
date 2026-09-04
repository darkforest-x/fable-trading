# BTCUSDT.P 15m live-regime entry gate

This experiment changes exactly one semantic eligibility rule from the frozen
parent regime episode: K2 may consume a regime only while EMA30 remains on the
same side of SMA60 and the four-bar EMA30 slope remains directionally aligned.

It does not change K1/K2 morphology, regime creation, rearm, position sizing,
cost, stop, runner, horizon, ACTIVE, frozen, forward, or live execution.

The zero-crossing liveness gate is fixed from the trend-strategy definition;
there is no parameter grid.  Development and the already-seen pre-holdout audit
are reported separately.  Repository holdout rows remain unread.
