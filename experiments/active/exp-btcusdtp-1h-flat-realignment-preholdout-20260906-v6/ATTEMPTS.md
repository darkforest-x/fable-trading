# Run attempts

1. Builder `5e70584`: process terminated with AssertionError during first
   immediate-case baseline parity, before any outcome table/summary was saved.
   Column `transition_armed_at` serialized CSV strings were compared to a mixed
   object Series of Timestamp/NaT. Printed clock values matched but scalar
   types differed. Preserved nine files under `attempt_01_parity_serialization/`:
   started receipt and eight already-derived request/context/status diagnostics.
   No new-policy P/L was printed or saved. This still read the development price
   prefix once; it is not an untouched-data attempt. No audit/holdout prices.

   Correction normalizes an object column only when every non-null replay
   scalar is a Timestamp. Both sides convert to UTC; all columns remain compared,
   including exact nanoseconds. A synthetic mixed-object/NaT case reproduces
   the failure and a +1ns mutation must still fail. No strategy, parameter,
   deadline, sample, input hash, matching or economic change. Commit this small
   verification correction before rerunning into a fresh `results/` directory.

2. Builder `824d2d1e771d6695491d07a992668feef9095462`: started
   `2026-09-06T04:03:15.759919Z`, completed with
   `rejected_development_no_audit`. Same frozen config and14 source hashes.
   Independent post-run checks found all eight pre-outcome derived files
   byte-identical to attempt1, all old outcome columns unchanged in the baseline,
   and original251/462 maternal denominators retained. New case47 requests,
   46 executions, one invalid risk and eight expired confirmations; controls67
   requests,61 executions,six invalid risks and22 expiries. No unknowns.
   The second read again materialized only219551 rows through2024-12-31 23:55UTC.
   No2025+ audit or holdout price read. Both attempts are disclosed, not merged
   into a fictitious first untouched-data run. See `VERIFICATION.md`.
