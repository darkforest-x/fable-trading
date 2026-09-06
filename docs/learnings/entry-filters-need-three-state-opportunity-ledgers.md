# Entry filters require three-state opportunity ledgers

- **Problem**: A filter can appear better by removing losses from its denominator,
  turning missing context into zero, or counting rejected opportunities as trades.
- **Dead end**: Reusing a full-entry ledger after filtering trades breaks its
  emitted-request contract. Reusing K2-expired status misstates why no trade exists.
  Keeping only selected returns hides foregone winners and breaks fixed controls.
- **Effective path**: Preserve every original request. Known aligned takes its
  actual execution; known opposite is observed nontrading zero with no fee;
  unknown remains NaN. Replay selected requests unchanged and reconcile every
  accepted old field. Each fixed control applies its own gate. Separate all-
  opportunity effects from actual-trade PF/count/month support.
- **General rule**: Before testing an entry gate, freeze its denominator and
  missingness semantics, including serial occupancy and rejected-control zeros.
- **Affected**: V13 prior4h colour helper, runner, saved-ledger audits; no global
  K2 status definitions or production rules changed.
