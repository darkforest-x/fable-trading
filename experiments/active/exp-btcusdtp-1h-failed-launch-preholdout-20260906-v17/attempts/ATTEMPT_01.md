# Attempt 01: stopped before the candidate

- Builder commit: `e5139c5f027401bd82f546f65ce2c813aac8fc1e`.
- Started UTC: 2026-09-06 11:26:38.757238.
- Terminal result: exit1, OFF baseline case-trades strict saved parity failure; candidate never invoked and no financial result selected/interpreted.
- Cause: CSV inference changed two opaque partial-fast source segment IDs from text to integers. A complete synthetic V16 CSV roundtrip reproduced exactly these two mismatches without market data.
- Preserved byte-for-byte files: nine files under `attempt_01_csv_segment_identity/`, including started/failure/context receipts and frozen contexts. The originally created baseline directory was empty. Files were moved recoverably, not rewritten or deleted.
- The run read the authorized 2023--2024 development price prefix. No 2025+ price values or holdout were consumed. This remains a real raw-replay attempt and is not omitted from run counts.
- Retry: same config and policy; scoped input converter plus strict negative regression tests only. The next run is raw attempt2 and the candidate's first execution. Old source hashes refer to the original commit; retry hashes are recorded separately.
