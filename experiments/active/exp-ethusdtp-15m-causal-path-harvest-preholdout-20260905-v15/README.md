# ETHUSDT.P 15m causal path harvest V15

V14 exposed an impossible retrospective contract: a later +8 ATR high cannot
undo size already banked before that high existed.  V15 makes the distinction
causal.  From +2 ATR onward, two shallow pullback stages bank 5% each.  Then the
first completed event wins:

- +8 ATR MFE first: lock the episode as a super-trend and cap total banking at
  10%, leaving at least 90% on the unchanged SMA60/ATR runner.
- the third pullback depth first: mark the original thrust as exhausted and
  unlock the 10% and 20% deeper-release stages.

The only selected parameter is base pullback depth.  Every release fills at
the next open and never changes the active stop.  Repository holdout is not
read and this experiment cannot promote automatically.
