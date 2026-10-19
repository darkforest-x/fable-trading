# Virtual market lists must preserve the whole navigation space

- **问题**：Rendering all 494 rows on every quote refresh multiplies layout and DOM work.
- **死胡同**：Simply slicing the first rows makes the remaining symbols and End-key target unreachable; replacing the whole visible slice also loses focus.
- **有效路径**：Rank the complete source universe, retain fixed row geometry and total spacer height, reuse visible nodes by symbol, and move the virtual viewport before focusing keyboard targets.
- **通用规则**：Virtualize rendering, not the source universe or keyboard navigation. Keep quotes and sorting freshness-aware.
- **牵连**：market-terminal.js/css, workspace-runtime.js; frontend_performance.test.cjs
