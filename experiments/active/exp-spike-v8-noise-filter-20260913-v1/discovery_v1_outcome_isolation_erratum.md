# Discovery V1 validation-outcome isolation erratum

`discovery_v1/signal_features.csv.gz` was produced by a runner that merged the
complete baseline trade ledger before writing its feature table.  Consequently,
its validation-period rows contain trade and outcome values despite the summary
tables being development-only.

This is a historical validation-outcome exposure.  The artifact must not be
described as blind or restored to blindness by the corrected runner.  This
erratum does not mutate `discovery_v1`; it records the exposure and leaves the
existing artifact available for audit.

The affected discovery manifest SHA-256 is
`75391cc1d1da4c701147acb77e65884ad4c05e26bb0d525237137e0a509ca098`.
The machine-readable companion is `discovery_isolation_erratum.json`.

The corrected discovery runner requires a physically separate
`baseline_trades_development` input and rejects a combined ledger before
feature collection.  It only merges development rows, writes
`outcome_available` and `outcome_withheld_validation`, and records
`validation_outcomes_present=false` plus `outcome_scope=development_only` in
new manifests.  Any new artifact remains distinct from `discovery_v1` and does
not erase the historical exposure.
