# ETHUSDT.P 15m decaying candle-color harvest V10

Profit excursions at +2/+4/+8/+12 signal ATR earn release slots. Completed
adverse-color candles release the slots at the next open. The four released
fractions follow a fixed 4:2:1:1 shape: the first warning banks the most, while
later warnings take progressively less from the trend runner.

Every fill only reduces size. It never changes the stop. The residual position
keeps the original SMA60/ATR exit. Only the maximum bank budget is selected,
and repository holdout is physically excluded.
