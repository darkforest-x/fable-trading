# SAHI can amplify object-boundary mismatch

## Problem

Dense regions looked like small-object candidates for sliced inference, but the
fixed full-validation SAHI run produced more boxes and fewer IoU50 matches than
direct YOLO.

## Approach

Run direct and SAHI on the same 1,255 images with the same E2.1b weights,
confidence and one-to-one IoU50 matcher. Keep the predeclared slice, overlap and
postprocess fixed, and first require direct inference to reproduce the existing
consistency artifact exactly.

## Result

Direct reproduced 665 matches from 1,629 predictions. SAHI produced 2,753
predictions but only 625 matches, reducing recall-like by 3.08 percentage points
and precision-like by 18.12 points while increasing latency 11.27 times.

## Reuse

Slicing is not automatically a recall improvement. When labels define merged
or tightly trimmed regions, local proposals can increase fragmentation and
false positives. Require an identical direct baseline and object-level matching
before promoting sliced inference.
