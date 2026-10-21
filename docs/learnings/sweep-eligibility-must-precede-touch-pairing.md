# Apply touch eligibility before choosing a signal pair

- **Problem**: Prior20-bar sweep morphology classified the already chosen touch candle. Turning that association into an entry rule can change which candle qualifies.
- **Dead end**: Filtering the old ledger retains55BTC/46ETH events, but discards arrows that have another eligible touch inside their original two-bar window.
- **Effective path**: Exclude non-sweeping touch rows before the existing earliest-close selector. This yields58BTC/54ETH events, including3BTC/8ETH replacement touches. Keep91 unchanged signal/control pairs cached and replay only11 changed pairs through the existing engine.
- **General rule**: A condition on a selectable candidate belongs before selection. Post-selection cohorts describe historical attributes; they do not necessarily implement the proposed strategy. Preserve key priority, time boundaries and risk matching, then compare both interpretations explicitly.
- **Scope**: `monday_range_sweep_reference.py`, `range_sweep_study.py`, fixed20 completed prior bars, original V2.31 pairing and exact-wick stops. Same known history; this correction does not create independent validation.
