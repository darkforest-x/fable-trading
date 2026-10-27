# Drawing completion must not block the next pan

- **问题**：Drawing and native chart gestures share pointer events. A finishing click can accidentally toggle chart focus, while the next intended drag must immediately resume normal chart interaction.
- **死胡同**：The first implementation suppressed every native event for 500 ms after completion/cancellation. The browser regression then failed: native options were restored correctly, but an immediate ordinary pan was still swallowed.
- **有效路径**：Own pointer/touch input only while placing or editing anchors. Restore the exact prior scroll/scale options on completion, cancellation, period/symbol change and teardown. After completion, suppress only the synthetic click/double-click; a new mouse-down, wheel or touch-start is allowed immediately. Keep the regression's immediate pan without adding a sleep that would hide the defect.
- **通用规则**：Debounce the specific unwanted follow-up event, not all input. A passed options/state assertion cannot substitute for performing the next real user gesture.
- **牵连**：`yoyo/monitor/static/chart-drawings.js`; `output/playwright/chart-drawings-20261027/fixture-qa.js`, `chrome-fixture-initial-pan-failure.log`, final Chrome/WebKit checks. Existing double-tap focus behavior is preserved.
