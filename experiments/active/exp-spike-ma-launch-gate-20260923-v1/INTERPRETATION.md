# Interpretation addendum — 2026-09-23

Written after run_v1 started and before full-run outcome statistics were built.
This records a wording ambiguity found in review; it is not retrospectively
represented as part of the original preregistration. Frozen source/config/plan
and run identity are unchanged.

- `ma_hard` deliberately removes all reference similarity: either 4-bar or
  5-bar geometry passing both sets of numeric hard gates admits the entry.
- `ma_grade_a` retains the original mining order: stage-1 hard conditions and
  distance <= 0.5 per geometry, choose minimum (distance, core length), then
  test stage 2 and reference quality on that selected geometry. No fallback
  to another geometry. The plan/config's unqualified core-selection wording
  applies to this full-selection arm, not to the reference-free hard arm.
- `ma_density_only` was specified as a non-primary diagnostic in the original
  plan's control section and config. The three named main arms plus this
  diagnostic make four actual replays. It cannot be selected as a winner by
  the two-arm primary significance test.
- Full Grade A here means the causal morphology/reference component only;
  future 3R outcome selection and retrospective score-ranked temporal NMS
  are excluded. Current-signal intersection is one particular adaptation,
  not a test of every possible delayed/latching integration.
- Current-source dual-side baseline is recomputed on frozen inputs. There is
  no prior V12.6 raw dual-side ledger or native Pine parity claim.
- All candidate gate decisions, fills and trades (including censored trades)
  are preserved. The reused engine does not emit reason-level status rows for
  every non-entry (occupied/invalid-risk/same-bar suppression); those causes
  must not be claimed individually audited. Its third returned table only
  records optional BE updates and is empty with this experiment's BE disabled.

No threshold, arm membership, primary test or execution rule was changed.
