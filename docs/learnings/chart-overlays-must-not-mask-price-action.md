# Chart overlays must keep price action legible

- **问题**：Filled weekly price pills covered candles. A new indicator popover also showed sharp chart marks through its nominally blurred material.
- **死胡同**：Price precision and correct ray anchors did not make opaque pills appropriate chart annotations. Increasing the child's blur radius alone did not fix a popover nested inside a filtered dock.
- **有效路径**：Draw Fibonacci-style name/price text above each bounded ray without any background rectangle. Keep the chart instance and viewport when toggling existing indicators. Put the dock's blur on a sibling pseudo-element, so the popover can sample the chart backdrop itself; verify both themes at actual mobile sizes.
- **通用规则**：Separate line annotations from event badges, and inspect the compositing ancestors of glass overlays. Verify canvas continuity, clipping and actual backdrop pixels rather than only CSS property values.
- **牵连**：`range-chart.js` shares weekly annotations across Monday, paper and patrol. `reclaim-chart.js` carries independent MA/internal-line visibility through updates and resume. Owner explicitly waived Stitch for this small revision on 2026-10-25 after the edit call timed out; this is not a standing waiver.
