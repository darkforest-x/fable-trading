# Optional regions must not block or remount primary content

- **问题**：The market waited for context and historical research before drawing its main chart; broad DOM observers and full-template parsing amplified every refresh.
- **死胡同**：Caching a whole fragment by its HTML still remounts an unchanged chart when a sibling navigation button changes. A fragment is not a component identity.
- **有效路径**：Render the primary view first, load research only when requested, reuse unchanged fragments before parsing, and retain child identity independently within changed fragments. Observe structural changes in the active page rather than every price/legend update.
- **通用规则**：Separate data availability, fragment parsing and persistent component identity. Validate navigation while optional requests are pending, not only a fully loaded screenshot.
- **牵连**：market-sync.js, workspace-layouts.js, symbol-picker.js, workspace-panels.js; regression tests retain chart identity and allow first render while optional context hangs.
