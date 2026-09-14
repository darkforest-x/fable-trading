# SPIKE V9 — Owner-selected entry bundle

## Authorization and scope

On 2026-09-15 the owner selected the asset/cost diagnostics and three fixed
entry-filter candidates, then explicitly requested “不需要对照啊 直接做” and
“升级为v9”. This authorizes USDC-base exclusion, confirmation RV>50 exclusion,
and UTC-Sunday scheduled-open exclusion as one combined V9 release. It
supersedes this task's proposal to run three independent forward comparisons.

The implementation is a new Pine v6 indicator and a Python admission adapter.
The original V8 files and archived research remain immutable. There is no new
performance experiment, parameter search, historical data load, model training,
execution-bundle promotion, account operation or notification creation.

## Frozen behavior

- Parent: SPIKE V8 with its existing V6 structure, BB context, 3ATR distance
  guard, risk calculation and original reversal feed.
- Base asset is explicit venue metadata; USDC as quote currency is allowed.
- RV is existing current volume / median of the preceding 20 valid volumes in
  the current contiguous segment. Exactly 50 is allowed; greater than 50 is not.
- Sunday is checked at signal close, the scheduled next opening instant on
  continuous time candles, in UTC. Beijing interval: Sunday08:00–Monday08:00.
  No claim about session-gap assets or delayed live fills.
- The replay adapter derives missing-bar flags from the arriving bar's clock
  and cancels pending entries through the parent gap handler. It does not use
  a later missing candle to rewrite an earlier admission decision.
- Missing base, RV or scheduled-open clock is labeled unknown and cannot pass.
- All three conditions are required for a new entry. A filtered raw opposite
  V6 event still terminates the original reference/trade according to its
  original engine; the filters do not change the upstream signal cooldown.
- Asset type, confirmation risk fraction and 20bp costR are informational.
  CostR is 0.002 / initial price-risk fraction. A signal-close reference is
  never called an actual next-open execution or an account risk percentage.
- Pine preserves V8's close-based visual reference. Python preserves the
  original next-open replay. This release does not claim cross-engine fill parity.
- Output tables carry explicit V9 version and arm labels while retaining the
  original engine's cohort/policy metadata as provenance.

## Acceptance

Use only generated synthetic inputs to test exact thresholds, base-vs-quote,
UTC/Beijing weekend edges, unknowns, overlaps, future mutation, reference costs,
reversal exits and immutable upstream state. Preserve raw helper/engine blocks
in the Pine source. Run relevant repository boundary and causality guards.
No new AUC, net return, PF, permutation significance or 10R retention numbers
are produced. The owner waived a new strategy comparison, not code verification.

## Delivery

Pine: `yoyo/evaluation/pine/spike_burst_v9.pine`.
Python: `yoyo/evaluation/spike_v9.py`.
Tests: `tests/evaluation/test_spike_v9.py`.
Report: `analysis/html/p1_spike_v9_implementation_20260915.html`.
Read the manifest and report for actual validation and deployment status.
