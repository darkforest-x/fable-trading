# Owner Notion ideas: timed breakeven and RSI reductions

Owner supplied the Notion source and explicitly authorized historical tests.
Follow-up: after the fifth5m candle, wait until price allows a cost-covering
protective stop. Opposite RSI diamonds reduce only when the per-unit close
profit exceeds the frozen round-trip cost. Five reductions each use20percent
of ORIGINAL size, not20percent of the remainder. Unprofitable signals consume
no reduction. The signal requires SAR direction flip and SAR<=30/>=70, not
ordinary small diamonds or a threshold on RSI itself.

Six prespecified arms isolate the time rule, the RSI timeframe3m/5m, and their
combination. This is explicit owner-authorized joint-rule testing; initial
stops, entries, costs and15m5R4ATR/H1-opposite runner remain unchanged.
No additional optimization grid is authorized by this plan.

Use existing same-source1m archive+confirmedAPI tail to reconstruct complete
3m and5m bars and validate all225384 frozen5m OHLC bars. Both RSI arms execute
on1m bars, so3m confirmed timestamps are never rounded to5m. Initial entries,
stops,15mATR andH1 events come from the frozen context, aligned by close time.
Carry forward no incomplete timeframe bar. RSI/SAR prefix causality is tested.

Reuse existing C++ stop/trail screener, extending only elapsed-time BE with an
explicit5m close clock. Independent size-only overlays select the firstfive
eligible events before the unchanged runner's terminal exit. Native Nautilus
HEDGING market reductions, fees, protection replacement and full closure
must match. No expected outcomes are supplied to the trading strategy.
Original5m baseline regression and1m-resolution differences are separately
documented; all primary arm comparisons use the common1m execution basis.

Report fixed-risk netR, matched controls, equal-notional bp, cash-only
drawdown, yearly segments, old10R/20R retention, rescued losers versus damaged
winners, and realized-profit capture.2024-only choice is inspected later;
history is already known, not a fresh validation set. Preserve failed tests
and outputs. No report/Pine/default/live changes requested. Register raw
outputs and Notion research results without overwriting the owner's ideas.
