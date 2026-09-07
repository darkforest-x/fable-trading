# Preserve and refine historical Owner boxes

Owner accepted historical-label reuse and explicitly requested box refinement on
2026-09-07. Preserve all 2,513 LONG/SHORT source boxes, original direction and
exact-star identity; account for the 12 skipped rows separately. First perform
geometry normalization of the established central core. Core placement remains
a visible review decision, not a result of a new automatic morphology score.

Keep source rectangles, baseline geometry, refined suggestions and subsequent
human answers separate. A source human rectangle does not turn a derived core
into human-confirmed Gold. No new onset is inferred from core_end+1. Retain the
17 equal-span candidate alias groups without silently merging different boxes.

Read only bounded pre-holdout OHLCV prefixes, verify source indices against the
Owner timestamps, and use the original prefix origin for moving averages. A
pre-holdout box endpoint does not authorize its whole historical W200 image:
three full originals cross the boundary and must never be opened by this build.

Deliver a separate Label Studio project with editable suggestions, the original
reference, a numbered comparison, and at most 40 future bars in a separate image.
The annotation canvas is not a new training input. Import suggestions through
the predictions protocol; preserve existing blank-label projects and answers.

Freeze source/config on main before building. Validate core containment,
outside-core invariance, image/time identities, full population coverage and
idempotent imports. Record the amount of geometric adjustment, remaining
semantic uncertainty and any truncated reference context in a Chinese HTML
report. This is label workflow work; no training, new model inference or
economic performance claim is included.
