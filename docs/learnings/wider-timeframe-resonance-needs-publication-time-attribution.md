# Wider timeframe resonance changes when a trade can actually be entered

- **Problem**: Adding H1 arrows within five hours of an existing 15m/5m pair sounds like a selection filter, but most matching H1 arrows may only become known after the original entry.
- **Dead end**: Selecting old fills using later H1 arrows leaves entry prices in the past. Lower total loss after removing trades also cannot establish improved trade quality.
- **Effective path**: Freeze each original 5m/15m pair, match inclusive H1 open positions, and enter only at the maximum of the three close times. Separate unchanged-entry subset attribution from executable delayed entries, recompute the same stop rule at entry, and compare average net R plus matched random entries. BTC515/616 andETH263/295 setups waited longer; total loss fell but average net R worsened. Deduplicating simultaneous confirmations remained negative.
- **General rule**: Every new resonance has a position window and a publication clock. Evaluate these separately; incomplete windows remain pending, and historical chart markers cannot establish past fill availability.
- **Scope**: `range_v237_triple_resonance.py`, experiment `exp-range-v237-triple-resonance-20261022-v1`; H1±5hours inclusive, original5m/15m±25minutes, native exits unchanged. This is historical research, not a Pine or production change.
