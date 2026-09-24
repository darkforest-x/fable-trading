# A fitted entry gate needs an explicit validation starting position

- **Problem**: A gate calibrated from an earlier period can change the position
  still open at the validation boundary. Replaying the fitted gate from the
  beginning of that earlier period lets later calibration observations influence
  a position that supposedly existed before the cutoff.
- **Dead end**: Filtering only the parent's closed trades loses candidates freed
  by an earlier rejected entry. Replaying all earlier candidates with the fitted
  threshold fixes occupancy but does not make that pre-fit history executable.
  Neither shortcut establishes an honest starting state for the later period.
- **Effective path**: Keep every authenticated candidate's fixed-entry outcome,
  separate the feature calibration from outcome selection, and explicitly inherit
  the same original baseline position at the fold boundary. Gates then arbitrate
  serial occupancy independently after that boundary. Check unfiltered replay
  against the original ledger, including blockers, censors and raw reverse exits.
- **General rule**: Before comparing a fitted gate with an existing strategy,
  specify when the gate becomes known and what happens to an already-open
  position. Early fitted performance is descriptive. Counterfactual cache reuse
  is valid only when the changed gate cannot alter a fixed entry's exit path.
- **Links**: `yoyo/evaluation/spike_v128_vwap_study.py`, its behavioral tests, and
  `experiments/active/exp-spike-v128-vwap-20260924-v1/PROJECT_PLAN.md`. This note
  records a simulation design constraint, not evidence of VWAP profitability.
