# Inclusive R thresholds need the same price-unit tolerance as order triggers

- **Problem**: A three-timeframe study's independent oracle disagreed with one native BTC exit. A 15m close exactly 1,063 points in profit on 212.6 points risk was mathematically 5R, but division produced 4.999999999999863.
- **Dead end**: Treating this as a changed signal or relaxing the exit-parity assertion would hide a deterministic boundary error. Re-running framework fills would not fix the oracle's comparison.
- **Effective path**: Check the exact candle, entry and stop, then compare directional price movement plus the existing tick-scaled tolerance against threshold times risk. Native Nautilus already used this comparison; align C++ and scalar verification only. Test both directions, exact threshold and one tick short.
- **General rule**: Inclusive R activation is a price-boundary decision. Use the same tiny price-unit tolerance as stop/target touch checks; never round R to a broad display precision or move a tradable boundary.
- **Scope**: `yoyo/evaluation/pepe_range_native.cpp`, `pepe_range_exits.py`, focused regression tests; native execution and strategy parameters are unchanged. Preserve old experimental outputs rather than overwriting them.
