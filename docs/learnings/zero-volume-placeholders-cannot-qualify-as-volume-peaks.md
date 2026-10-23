# Zero volume placeholders cannot qualify as volume peaks

- **Problem**: The owner requested a pin whose volume is the maximum of21candles. Every volume field in the existing HistData spot XAUUSD archives is zero.
- **Dead end**: A literal `volume >= rolling_max` returns true for every all-zero window. That creates apparent high-volume signals from data that contain no volume information.
- **Effective path**: Audit the source volume column first, require observed positive volume for the peak comparison, and declare the spot-gold volume hypothesis unavailable. Do not silently substitute gold perpetuals, tick counts, or a fabricated proxy.
- **General rule**: Before applying a relative filter, establish that the underlying measurement exists and has variation. Passing an inequality is not evidence of measurement availability.
- **Related**: `yoyo/evaluation/monday_15m_volume_rejection.py`; `exp-monday-15m-volume-rejection-20261023-v1/gold-volume-availability.json`; all raw inputs remain unchanged.
