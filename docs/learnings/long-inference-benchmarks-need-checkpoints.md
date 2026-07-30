# Long inference benchmarks need per-image checkpoints

## Problem

A full direct-versus-SAHI validation runs several predictions per image. A
single late failure would otherwise discard hours of valid work and encourage
switching to an unrepresentative small sample.

## Approach

Write one immutable JSONL record after every image and keep separate direct and
SAHI checkpoints. On resume, reject rows from another mode or image set, then
continue only missing images while preserving the deterministic manifest order.

## Verification

The 10-image E2.1b smoke run produced exactly 10 direct and 10 SAHI checkpoint
rows, then reconciled both files into one summary without rerunning completed
images.

## Reuse

Use per-item checkpoints for long read-only evaluations. Keep the experiment
configuration beside the checkpoints and abort rather than mixing incompatible
runs.
