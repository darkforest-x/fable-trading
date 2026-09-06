# Future dtype promotion can break historical artifact parity

- **问题**：A causal source-zone generator ignored future rows correctly, yet
  replacing future numeric colours with NaN changed the dataframe dtype of
  earlier emitted diagnostic colours. Prefix outputs were no longer identical.
- **死胡同**：Only checking whether future values enter the entry predicate.
  Dataframe dtype inference works at column scope, so an ignored suffix can
  still promote integer values to floats before row serialization. It need not
  alter P/L to break an exact source contract or a downstream type-dependent gate.
- **有效路径**：Normalize emitted numeric diagnostic scalars to the declared
  output type, while preserving numeric values and unknowns; then compare full
  output frames, not only signal IDs, for prefix and poisoned-future variants.
  Keep future OHLCV/features unvalidated and unread until their own availability.
- **通用规则**：Causality tests should cover signal membership, timestamps,
  feature values **and schema/dtypes**. Stable math alone is weaker than stable
  artifacts. Never fix this by dropping troublesome comparison columns.
- **牵连**：`yoyo/data/hourly_impulse_source_zone.py` and
  `tests/test_hourly_impulse_source_zone.py`, especially future NaN poisoning.
  V7 remains preregistered research; this correction used synthetic data only.
