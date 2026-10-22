# Trade paths must stop at the rendered candle boundary

- **Problem**: A fixed60-bar review chart can contain a position that remains open beyond the final visible candle. Drawing its full later stop path extends prices into the right-hand label margin without the corresponding candles or time labels.
- **Dead end**: Clipping to the plot axes is insufficient when the axes include a deliberately empty margin for Fib labels. The technical plot limit and the evidence window are different boundaries.
- **Effective path**: Clip the displayed stop path at the final visible candle close, carry forward only its already-known protection price, and label the full-path outcome as outside the window. Keep the complete native ledger unchanged.
- **General rule**: A visual evidence window constrains overlays as well as candles. Layout padding must never silently become extra time-series coverage.
- **Scope**: `range_v236_charts.py`,202landscape5m review images. Signal counts, native execution paths and delivered Pine source are unchanged.
