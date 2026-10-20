# Entry-candle structural stop

- Owner asks whether the entry candle's extreme works better than the current five-candle extreme.
- Change only the structural window, 5 to 1. Keep the 0.05% entry-price buffer, outward 0.01 tick rounding, entries, controls, 15m 5R/4ATR management, armed H1 reversal exit, and 0.2% round-trip cost.
- Compare within each existing exit policy: no breakeven and 25-minute fee-aware breakeven. Do not add RSI reductions or change Pine/defaults.
- Reuse frozen 1m features and old ledgers. Run only two new Nautilus arms, verify against the existing C++ engine, and retain all fills.
- Report the requested Aug23–Sep22 Shanghai entry cohort and all historical years separately. Keep paired random controls, entry IDs, forced boundary exits, and fee/R-denominator effects visible.
