# Wick-based R becomes available at the touch candle's close

- **Problem**: A resting 1.618 limit fills before the touch candle has finished. Its final adverse extreme cannot determine an initial stop or position size at that fill.
- **Dead end**: Using the final wick to place a stop at entry, or awarding a target touched earlier in that candle, would import information from after the decision. This alternative was rejected before historical execution.
- **Effective approach**: Use a predeclared percentage stop at the fill. Freeze wick R only at the completed touch close and activate the target then; a target already marketable at activation receives taker execution at that close. Keep the immediate next-bar confirmation and make fee protection effective only after a completed close has enough room.
- **General rule**: Record availability, activation, and fill times separately for every risk parameter. Ex-post R normalization does not make pre-entry fixed-risk sizing feasible. A fee-neutral stop can still lose through a gap or separately modeled slippage.
- **Related**: `yoyo/evaluation/monday_wick_r_engine.py`, `tests/evaluation/test_monday_wick_r_engine.py`, `exp-monday-5m-wick-r-20261026-v1`. Owner selected causal touch entry with initial percentage protection on 2026-10-26.
