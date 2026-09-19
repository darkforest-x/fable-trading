# Independent Luna Max review

Reviewer: `/root/v9_htf_sma_mapper`, budget-mapper role (gpt-5.6-luna, max). Read-only. No nested delegation or source edits.

Finding: no blocking issue for the report's qualified mechanism conclusions. Reviewer independently ran the6 tests, all passed; checked87-stream path, temporal split additivity, independent arms, graphical encoding and purposefully selected past-only windows.

Confirmed rates:49.05%/48.51%/48.23% of shared-known bars are old density; group-connected arm removes57.4%-59.0% of old density. Retention is not precision. Stable-ATR arm has added bars and is correctly excluded from the retention-only chart. Identity-permutation null is a geometry mechanism control, not market labels or profit evidence.

Limits found:

1. `legacy_raw` lacks a known/valid gate; recent masks and row-age use it. Age globally forward-fills and does not reset at gap; cannot generally interpret row count as elapsed time. Existing gap test covers known rewarm only.
2. Aggregation retains6 1h/10 4h partial buckets and does not split their count by earlier/later. EMA retains pre-gap state under original semantics. Call denominator shared-known bars, not complete original bars.

Parent disposition: both limits disclosed in report. All main counts apply the known mask. Additional artifact assertion: all4,616 V9 candidates are known, ages nonmissing and all<339 (maximum69); hence their most-recent density falls inside the340-valid-bar contiguous segment. No gap crossing occurs for this run's candidate ages. This narrows the current impact without claiming the general age implementation is gap-safe. No source change or rerun was needed for descriptive conclusions.

Anchor boundaries remain unconfirmed proposals. No confusion matrix, accuracy claim or automatic promotion.
