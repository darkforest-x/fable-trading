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
