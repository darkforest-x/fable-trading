# Theme colors must remain live across component boundaries

- **问题**：The route shell was dark, but lazy study playbooks still used fixed light-theme text/tables. A body-level diagram dialog copied computed reader colors when opened, so later theme changes could not reach it.
- **死胡同**：Checking only default route screens missed the failing secondary tabs. Recoloring the page background could not fix inherited text, figure tokens, or inline color snapshots.
- **有效路径**：Exercise the actual secondary tab and open dialog. Bind reading surfaces to semantic tokens and share live CSS aliases between the reader and its portaled dialog; remove the computed-color copy. Verify a light/dark roundtrip with the same dialog still open, plus lazy navigation and shadow-root settings.
- **通用规则**：Theme state belongs to the application; component palettes must stay reactive across mount, portal and shadow boundaries rather than capture one rendered color snapshot.
- **牵连**：`trading-playbooks.css`, `rektproof.css`, `rektproof.js`, `design-tokens.css`; `output/playwright/night-mode-20261027/` records the original failures and browser checks. Layout, chart data and trading behavior are unchanged.
