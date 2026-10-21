# A confirmed pivot exit cannot fill at its retrospective extreme

- **Problem**: A 15m pivot with two right-hand bars is drawn at an earlier high or low. Treating that drawing as an executable exit overstates the available profit.
- **Dead end**: Passing the pivot price as a preknown limit target would let orders use information that did not exist when the price occurred.
- **Effective path**: Expose the pivot only at the second following close. Keep the original native protective stop active through that bar, then submit a market exit for surviving positions at the confirmation close. The pivot's center must open at or after entry. A profitable historical extreme can therefore produce a losing realized exit.
- **General rule**: Store feature confirmation time, retrospective anchor time and fill time separately. Test prefix invariance, both directions, and a stop inside the confirmation candle. Treat an undocumented tie rule as a sensitivity, not established platform parity.
- **Connections**: `yoyo/layers/l3_backtest/nautilus_independent.py`, `tests/evaluation/test_nautilus_pivot.py`, `yoyo/evaluation/range_v230_pivot_study.py`. No Pine signal or live-trading default is changed.
