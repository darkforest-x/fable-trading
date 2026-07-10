# Ultralytics list sources must be chunked

## Problem

Passing all 5,900 validation image paths to one Ultralytics classification
`predict` call attempted to allocate an approximately 9 GiB image buffer before
streaming results. The model fit in memory, but source construction did not.

## Approach

Split manifest-ordered paths into fixed batches before calling `predict`, then
append each streamed result in the same order. Keep the model batch size equal
to the path chunk size so memory is bounded independently of validation-set
size.

## Verification

- A unit test proves chunking preserves order and includes the final short batch.
- The full 5,900-row validation evaluation completed in 37.6 seconds with
  exactly 5,900 predictions.

## Reuse

Use bounded source chunks whenever an Ultralytics API receives a Python list of
many image paths. `stream=True` alone does not guarantee bounded source-loading
memory.
