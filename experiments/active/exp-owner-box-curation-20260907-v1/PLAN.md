# Audit the existing Owner review pack with installed dataset tools

Owner authorized the backend curation workflow on 2026-09-07. The scope is the
2,513 primary annotation images and unconfirmed proposals in the frozen Owner
refinement pack. Existing Label Studio project77 remains the only review entry.

1. Freeze source/config before actual execution. Verify all image identities,
   source times and unconfirmed-label semantics; never import future/reference
   images as additional samples or pretend proposals are model predictions.
2. Use installed Datumaro for detection format/geometry validation and CleanVision
   for exact duplicates, perceptual-hash duplicate candidates and size/aspect
   issues. Retain every original review ID and preserve all source bytes.
3. Publish a separate persistent FiftyOne backend dataset with proposal boxes,
   audit tags and named views, using the same database as the configured MCP.
   No learned embeddings or model downloads are needed for this bounded pass.
4. Combine tool findings with previously frozen negative-window conflicts and
   descriptive envelope expansion. Produce a deterministic first50 review queue
   and named filtered tabs inside project77; do not modify tasks or annotations.
5. Assess whether Cleanlab has eligible out-of-sample detection predictions.
   If unavailable, record the evidence rather than substituting geometry proposals
   or reusing unrelated classification predictions. Do not train to manufacture
   eligibility during this audit.
6. Validate actual SDK controls, all identities, MCP counts, view membership and
   non-destructive re-entry. Deliver a Chinese HTML report, registered evidence
   and a learning note. Preserve concurrent work and stage only owned paths.

This is a workflow/data-quality audit, not a model-performance experiment. No
images are deleted, no classes or core bars changed, no train/val split created,
no holdout OHLCV opened, and no training or production eligibility promoted.
