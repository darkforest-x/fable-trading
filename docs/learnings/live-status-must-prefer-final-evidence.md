# Live status must prefer final evidence

## Problem

The VPS pipeline was healthy but still selected the older E2.1 report and said
E2.1b was under observation after E2.1b, SAHI and direction evaluation had all
finished. Deployment success therefore exposed stale project truth.

## Approach

Order evidence candidates newest-final-first, include the current metric key
shape, and make the gate note describe the final failed decision. Protect the
selection with an isolated test that supplies both the final report and metrics.

## Verification

The local stage now selects `p2a_e21b_hsv0_report.md`, exposes mAP50 `0.8505`
with the failed gate, and no longer says training is in progress. VPS is
redeployed and checked through the public redacted endpoint.

## Reuse

Operational dashboards must prioritize finalized evidence over older artifacts.
A green service check is insufficient when the displayed decision is stale.
