# Time-window audits must compare like boundaries

## Problem

A strict chronological split audit rejected one valid Stage A validation negative even though its complete image window stayed inside the validation exposure block. The global full-window split was clean, but the negative sub-audit reported that the sample began before validation.

## Dead-end attempt

Treating the audit failure as proof that the builder leaked across the split would have moved or dropped a valid sample without identifying the actual invariant. The failing comparison used the negative window's `start_time` against the earliest positive window's `end_time`, so it compared two different boundaries.

## Effective approach

Express the containment rule with like-for-like boundaries:

- validation negative `start_time` must be at or after the earliest validation positive `start_time`;
- validation negative `end_time` must be at or before the latest validation positive `end_time`;
- the latest train window end must still precede the earliest validation window start.

Add a regression case where a negative begins after the first visible validation bar but before the first validation image ends. That sample is inside the validation block and must pass.

## General rule

For interval audits, compare starts with starts and ends with ends. A decision timestamp or window end is not a substitute for the first visible bar. Name derived variables after the exact boundary they contain, then test a partially overlapping-in-time but fully contained interval to expose boundary mix-ups.

## Implications

The corrected audit does not relax the train/validation embargo. It only makes the validation-negative containment check match the declared full-window time block. Historical audit results produced by the earlier comparison may contain false negatives and should be regenerated before citing that specific gate.
