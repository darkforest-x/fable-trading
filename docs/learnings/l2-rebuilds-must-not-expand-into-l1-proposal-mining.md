# L2 rebuilds must not expand into L1 proposal mining

- **Problem**: A full P1 L2 rebuild was initially interpreted as scanning every
  eligible historical window in the current 344-symbol live universe. That is
  a new L1 proposal-mining run, not a rebuild of the frozen L2 candidate data.
- **Evidence**: The P1.0 snapshot names `data/judgment_v10_wide.csv` as the
  frozen causal-tip proposal ledger: 18,379 unique pre-holdout proposals across
  232 symbols. Its source generator rendered the exact window ending at each
  proposal tip. The current 344-symbol universe is an authority for allowed
  coverage, not an instruction to invent proposals for its 112 zero-fire
  symbols.
- **Resolution**: Replay only the exact causal window named by every frozen L1
  proposal, remap its current detector box through the shared live operator,
  then rebuild canonical features, labels, and costs. Account explicitly for
  every proposal that produces no accepted box or no labelable row. Record all
  344 universe symbols, including zero-proposal symbols, without reading their
  candle files.
- **Guardrail**: A stage boundary needs two separate manifest fields:
  `universe_symbols` (what is allowed) and `candidate_source` (what is consumed).
  If an L2 rebuild schedules windows not named by its frozen proposal source,
  stop: the task has silently become L1 mining.
