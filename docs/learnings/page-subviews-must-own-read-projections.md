# Subviews must own their polling and response projections

- **问题**：A visible detection workspace kept polling the full private account while its model documentation was open; result headers also downloaded hidden tables.
- **死胡同**：Document visibility alone cannot distinguish a visible workspace from its hidden internal panes. Hiding tables with CSS does not stop their fetches or rendering.
- **有效路径**：Own activation at the internal tab boundary. Results request a small projection with the original account freshness and entry readiness, account details request the full view, and other tabs request neither. Keep summary and full snapshots separate so a summary cannot replace position data.
- **通用规则**：Match read scope to the visible surface. Display optimizations must not loosen execution gates, combine accounts, or reuse display snapshots as executable quotes.
- **牵连**：manual-account.js, yolo-detect.js, manual_api.py, manual_dashboard.py; GET-only, source freshness and manual confirmation remain unchanged.
