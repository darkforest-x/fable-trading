# Historical morphology reuse audit

This is a completed lineage and feasibility audit, not a fitted classifier.

- Report: `analysis/p1_ma_morphology_reuse_20260920.md`.
- `inventory_v1/` preserves the literal legacy-path inventory.
- `recovery_v2/` preserves explicit archived-image and same-feed timestamp recovery.
- Builders were committed before their runs: inventory `472f189e02ffd3214fda5d2001bcad5b6fa6880a`, recovery `e06b10d6c0bf50d567a265939f90f7ef27815c80`.
- Notion: https://app.notion.com/p/3e08856479af8145a211de5fd0e7eac4

## Evidence checks

221 distinct reviewed events (79 positive, 142 negative) have exact original image hashes and complete timestamp mappings into 126 current OKX 15m files. Overlapping candidate files disagree on zero mapped windows. 51 chosen-source event indices shifted relative to the legacy manifest. Original OHLC hashes are unavailable; timestamp recovery is not historical numerical byte parity. The original manifest remains unchanged.

Source output hashes and clock ordering were checked. Luna Max independently audited feature dimensionality, pre-core visibility, direction semantics, target meanings, and historical negative experiments; the parent verified source records and recovery outputs. No new model, eligibility, trading result, Pine change, or HTML was produced.

79 positive boxes have rule-derived boundaries; original positive views contain 3–5 post-core bars. Original validation contains only eight positives. Recovered labels require a view/target audit before reuse as causal morphology supervision. Starred references have 161 available original images out of 176, but full numerical linkage was not done in this audit.
