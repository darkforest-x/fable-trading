# Fees that set protection prices require a new execution replay

- **Problem**: The latest owner instruction sets research costs to zero, while a saved Monday study still uses20bp for entry feasibility and TP-triggered breakeven.
- **Dead end**: Adding fees back to saved net returns produces gross returns for the old executions; it does not move the protective stop to the entry price or reconsider rejected entries. Reusing native fills by matching entry and target prices alone misses this policy change.
- **Effective path**: Include fee changes in admission invalidation; forbid native projection across different fee/protection policies. Replay the zero-fee baseline once, then project only the unchanged positions retained by the causal2percent pre-entry filter.
- **General rule**: Before applying a cost instruction, determine whether costs are reporting-only or affect a decision/barrier. In the latter case replay affected execution, retain the old version and compare on a consistent cost basis.
- **Scope**: `monday_fib_tp_breakeven.py`, native TP protection, fee-space admission and latest owner2026-10-22 instruction. Historical20bp artifacts remain unchanged.
