# Sparse execution deltas can have empty reporting groups

- **Problem**: Replaying one changed ETH entry succeeded in Nautilus, but its JSON receipt failed because the unused direction and chronological-half groups produced NaN means.
- **Dead end**: Assuming every delta contains both directions and halves makes valid execution results impossible to serialize. Filling absent observations with zero would invent performance.
- **Effective path**: Omit empty groups, retain observed groups, and test a single signal/control pair with strict JSON serialization. Preserve the incomplete output; reuse completed pivot paths and rerun only the incomplete exit arm.
- **General rule**: Incremental execution subsets need not have the full study's category coverage. Empty group means are unavailable, never zero.
- **Related**: `yoyo/evaluation/range_v230_exit_compare.py`, `tests/evaluation/test_range_exit_sparse_summary.py`, and `exp-range-v231-fib0618-20261021-v1`.
