# Time-gated diagnostics should self-seal

## Problem

The q80 loop refreshed data and the shadow ledger, but reaching the predeclared
24-hour boundary still depended on a person noticing the clock and exporting a
snapshot. Later cycles could then silently change the evidence window.

## Approach

Use market bar time rather than wall-clock time. Every cycle writes a disposable
readiness status, and the first cycle at or beyond 24 market hours atomically
creates one fixed snapshot that later cycles never overwrite.

## Verification

Tests cover the 23.75-hour not-ready state, the exact 24-hour seal, fixed-cost
q90 versus q80-only economics, and duplicate signal rejection. The live cycle
produces a not-ready status without modifying ACTIVE or the main forward books.

## Reuse

For time-gated forward experiments, make the first admissible snapshot immutable
and derive readiness from event time. Scheduled execution should remove the need
for human timing without weakening the experiment gate.
