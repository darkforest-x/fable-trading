# Final-only report sections need runtime source-scope checks

- **Problem**: The MA6 report worked with partial data, but its complete result showed a blank page. The final interpretation mounted a source map containing all nine queries while declaring only two.
- **Dead end**: A 452 MiB snapshot suggested a size problem. Projecting duplicate ledger fields reduced it to 182 MiB without losing rows, but did not fix the blank page. Size was not the demonstrated cause.
- **Effective path**: Read the browser error first. The shared runtime named `parent-interpretation` and its undeclared `rules` query. Bind only the actual interpretation sources and declare the exact same query set. Check the complete-data rendering path rather than relying on a successful build or partial preview.
- **General rule**: For a blank data app, inspect the first error message before attributing it to payload size. Conditional sections need final-state browser validation. Print only the error's first line: data-URL stacks can contain an entire bundled runtime.
- **Scope**: `reports/monday-ma6-retest-20261024/src/content/report/ReportContent.jsx`; the separate CSV exports retain every original field, while the browser's explicitly disclosed projection retains every row.
