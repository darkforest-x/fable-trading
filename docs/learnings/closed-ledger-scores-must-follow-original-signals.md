# Closed-ledger scores must follow their original signals

- **问题**：Touch and open records carried entry scores, but closed positions lacked them, preventing consistent display and sorting across the ledger.
- **死胡同**：The browser's recent-touch page contains at most 100 signals. Looking up closed-position scores there would silently lose older scores or associate another occurrence of the same symbol.
- **有效路径**：Join each closed position to its original signal ID in the read-only snapshot. A left join preserves legacy positions without a matching score. Sort displayed rows by raw finite score, then time; unknown scores remain unknown and last. Highlight strictly greater than 0.5 without changing any execution threshold.
- **通用规则**：Presentation fields that describe a historical decision belong to that decision's immutable identity, not a recent-page cache or a symbol-level lookup. Row selection and external links must retain separate hit areas; see [external hit targets](flexible-layout-must-not-expand-external-hit-targets.md).
- **牵连**：`monday_reclaim_paper.py`, `monday-reclaim-paper.js`; partial/full-close snapshot regression and all-three-tab score-order tests. No fill, sizing, fee or account-state mutation, 2026-10-26.
