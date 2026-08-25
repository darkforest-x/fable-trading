# Top-20 expansion result

Status: **failed closed**.

The authorized configuration-use #2 completed the frozen 54-symbol scan, but
the SHORT side had only 15 candidates after the unchanged 18-bar same-symbol
deduplication rule. The preregistration required exactly 20 per side, so the
builder raised before writing a candidate manifest, chart, contact sheet, null
control, or successful scan summary.

This failed run still consumed holdout because it read 36,720 4h symbol-rows on
and after 2026-05-04. The exact receipt is `failure_receipt.json`.

Do not weaken deduplication or silently return asymmetric Top-N after seeing the
result. Any smaller or asymmetric configuration requires a new frozen contract,
explicit Owner authorization, and holdout consumption #3.
