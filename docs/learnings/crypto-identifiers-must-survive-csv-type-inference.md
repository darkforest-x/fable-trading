# Crypto identifiers must survive CSV type inference

- **Problem**: Strict account/replay identity validation rejected Binance `4USDT`: the frozen asset identifier was the string `4`, while CSV inference converted it to an integer.
- **Dead end**: Relaxing identity equality or coercing an entire table would weaken provenance checks or corrupt numeric calculations. Missing-value recognition can also erase a legitimate `NA`-like identifier.
- **Effective path**: Declare identifier columns as literal strings at the CSV boundary, while keeping numerical missing-value handling intact. A round-trip regression checks both `4` and `NA` and an empty numeric cell.
- **General rule**: Identifiers have a schema even when their characters look numeric or resemble missing-value tokens. Parse that schema explicitly before comparing provenance.
- **Related files**: `yoyo/evaluation/spike_v1_plus_report.py`, `tests/evaluation/test_spike_v1_plus_report.py`.
