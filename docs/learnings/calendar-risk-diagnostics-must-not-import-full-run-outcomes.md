# Calendar risk diagnostics must not inherit full-run future outcomes

- **问题**：An account report split NAV into development and validation correctly, but spread the full-run `ruined` and allocation metadata into both period rows. A later validation ruin would appear in development risk statistics.
- **死胡同**：Checking that return calculations use the right date slice is insufficient. Metadata attached after the calculation can still import future outcomes even when the financial arithmetic is causal.
- **有效路径**：Compute period ruin and scheduled-entry counts from the period's NAV and entry timestamps. Prefix genuinely full-run leverage, allocation and reconciliation fields with `full_run_`. Add a synthetic account that gains in development and reaches zero only in validation; assert development remains non-ruined.
- **通用规则**：Before publishing a time-split report, audit every output column's time scope, including diagnostic flags and counts, not just the central performance metric.
- **牵连**：`yoyo/evaluation/spike_exit_post.py`; `tests/evaluation/test_spike_exit_post.py`; development policy selection must not read later outcomes.
