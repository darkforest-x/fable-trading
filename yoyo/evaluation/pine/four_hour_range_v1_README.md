# New York first-four-hour range, Pine v1

`four_hour_range_v1.pine` is a TradingView strategy companion to
`yoyo/evaluation/four_hour_range.py` and the preregistered experiment plan at
`experiments/active/exp-four-hour-range-20260920-v1/PROJECT_PLAN.md`. It is a
research display and broker-emulator check, not a production strategy or an
exact Python parity claim.

Use it only on a standard 5-minute chart. The script fails at runtime on any
other timeframe or non-standard chart. Its default research window is New York
midnight 2023-09-20 through, but excluding, New York midnight 2026-09-19.

The reference range is `America/New_York` 00:00–04:00 wall-clock time. Pine
calculates the expected bar count from those IANA-tz timestamps, so DST days
expect 36 or 60 5-minute bars and ordinary days expect 48. It also requires
every observed reference-bar timestamp to be contiguous. A changed NY date
always clears the prior range before reference construction. Therefore charts
that start after 00:00, omit a reference candle, or omit the entire reference
period fail closed: they produce no usable range that day.

The strategy uses no indicators, volume rule, subjective order blocks, trend
filter, pyramiding, or account-sizing logic. A lower/upper strict close arms a
future long/short return. The first strict close back inside queues next-bar
market entry; the full wick from the first outside close through the return bar
freezes the stop; the target is 2R from the actual filled entry; NY 23:55 closes
the position. `qty=1` is a clear accounting unit only. `initial_capital=10m`
exists so a one-BTC test order is not silently rejected by the broker emulator;
it is not an allocation, leverage, or account-size recommendation.

## Native differences from the Python evaluator

- Python treats a same-5m stop/target double touch pessimistically as stop
  first. TradingView uses its broker emulator path and can differ.
- Python applies a fixed 20bp round trip. Pine charges 0.1% on each executed
  order, so realized total commission can differ with exit fills and notionals.
- Python validates source volume and can mark a partial final NY day as
  `data_end`/censored. Pine has neither a source-volume contract nor a
  censoring ledger; NY-EOD immediate closing follows the broker emulator.
- Python rejects an adverse next-open fill when the frozen stop gives
  non-positive risk. Pine cannot know that next open before filling the market
  order. It immediately closes the already-filled position and labels it
  `INVALID NEXT-OPEN RISK`.
- `calc_on_order_fills` is used only to obtain actual entry price and install
  its frozen-stop/actual-2R bracket. The fill callback deliberately does not
  inspect current-bar high, low, or close or advance the range/excursion state.
  `varip` position-change facts and `lastProcessedBar` keep those callbacks
  from consuming historical bar OHLC.

## Native check on 2026-09-20

The new private script `NY 首4小时区间假突破回归 · v1`, revision 1,
compiled and ran on standard OKX BTC 5m. TradingView displays its generic
look-ahead caution for the order-fill recalculation setting. This is not a
full parity certificate: on NY 2026-09-17 the native plotted reference high
was 76648.7 while the independently checked OKX API range high was 76655.2.
Native raw-history export required a Premium upgrade and was not completed.
Source and execution differences therefore remain unisolated. Use the frozen
Python ledgers for the reported three-year economic result.
