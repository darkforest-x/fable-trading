# ETHUSDT.P 15m micro profit ladder V16

This is the conservative answer to the Owner's “slowly TP, keep the trend”
requirement.  It uses four real partial exits at +2/+4/+8/+12 signal ATR and
leaves at least 80%--90% of the original position on the unchanged SMA60/ATR
runner.  Selection chooses only total bank size from 10%, 15%, and 20%, subject
to retaining at least 95% of the baseline 95th-percentile winner.

There is no break-even floor, no profit ratchet, and no stop change after any
partial fill.  Repository holdout is physically excluded.
