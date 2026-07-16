# fable-trading Design System

## 1. Atmosphere & Identity

A quiet trading command center for daily evidence checks. The signature is restrained density: dark surfaces, compact metrics, and sober status color that make weak or incomplete evidence feel visible instead of hidden.

## 2. Color

### Palette

| Role | Token | Light | Dark | Usage |
|------|-------|-------|------|-------|
| Surface/primary | --bg | N/A | #131519 | Page background |
| Surface/secondary | --surface | N/A | #1b1e24 | Panels and charts |
| Surface/elevated | --surface-2 | N/A | #22262e | Tiles, active controls, row hover |
| Text/primary | --text | N/A | #e8e9eb | Primary labels and values |
| Text/secondary | --text-2 | N/A | #9aa0a8 | Notes, metadata, muted labels |
| Border/default | --border | N/A | #2e3340 | Panel borders and table dividers |
| Accent/primary | --accent | N/A | #3987e5 | Active informational focus and chart line |
| Status/success | --up | N/A | #1fa77d | Positive returns, TP, passed checks |
| Status/error | --down | N/A | #e66767 | Losses, SL, failed checks |
| Status/warning | --warn | N/A | #c98500 | Timeout or caution states |

### Rules
- The dashboard is dark-only.
- Status colors represent evidence states, never decoration.
- New colors must first be added here and should map to a semantic status or data encoding role.

## 3. Typography

### Scale

| Level | Size | Weight | Line Height | Tracking | Usage |
|-------|------|--------|-------------|----------|-------|
| H2 | 15px | 600 | 1.6 | 0 | Panel headings |
| Body | 14px | 400 | 1.6 | 0 | Default UI text |
| Body/sm | 13px | 400 | 1.55 | 0 | Table rows, controls |
| Caption | 12px | 400 | 1.4 | 0 | Units, notes, tile labels |
| Numeric/lg | 22px | 600 | 1.3 | 0 | Main tile values |

### Font Stack
- Primary: "PingFang SC", "Hiragino Sans GB", "Microsoft YaHei", system-ui, sans-serif
- Mono: ui-monospace, SFMono-Regular, Menlo, monospace

### Rules
- Use tabular numeric rendering for financial values and sorted numeric columns.
- Do not scale type with viewport width.
- Keep dense dashboard copy short enough to scan in Chinese.

## 4. Spacing & Layout

### Base Unit
All spacing derives from a base of 4px.

| Token | Value | Usage |
|-------|-------|-------|
| --space-1 | 4px | Tight inline gaps |
| --space-2 | 8px | Compact control and legend gaps |
| --space-3 | 12px | Grid gaps and toolbar spacing |
| --space-4 | 16px | Panel inner padding |
| --space-5 | 20px | Main top padding |
| --space-6 | 24px | Page horizontal padding |
| --space-10 | 40px | Large vertical separation when needed |

### Grid
- Max content width: 1280px
- Primary layouts: 4-column stage/tile rows, 2-column analytical panels, 1fr + 280px signal detail rail
- Breakpoints: collapse to 2 columns under 980px, then single-column for chart/detail layouts

### Rules
- Fixed-format elements such as charts, tables, segmented controls, and metric tiles keep stable dimensions.
- Page sections are unframed layout regions; panels frame analytical tools.

## 5. Components

### Topbar Tabs
- **Structure**: sticky topbar with brand on the left and `.tabs > .tab` buttons on the right.
- **Variants**: active, default.
- **Spacing**: `--space-3` vertical topbar padding, `--space-1` tab gap.
- **States**: hover raises text contrast; active uses `--surface-2` and `--border`.
- **Accessibility**: buttons remain native buttons; active state must also be represented by visible contrast.
- **Motion**: no motion.

### Segmented Control
- **Structure**: `.seg` wrapper with adjacent button segments.
- **Variants**: active/default; used for cost, window, outcome, bar range, universe.
- **Spacing**: 6px x 14px button padding.
- **States**: hover, active.
- **Accessibility**: native buttons; labels must be explicit.
- **Motion**: no motion.

### Panel
- **Structure**: `.panel` with optional `h2`, controls, chart, table, or metric grid.
- **Variants**: default, side, grow.
- **Spacing**: 16px x 18px default padding.
- **States**: loading opacity is allowed on the view wrapper.
- **Accessibility**: headings name the analytical region.
- **Motion**: no motion.

### Metric Tile
- **Structure**: `.tile` with `.lbl`, `b`, and `small`.
- **Variants**: positive/negative value, neutral value.
- **Spacing**: 12px x 16px padding.
- **States**: static; no hover unless tile becomes clickable.
- **Accessibility**: value and subtext must be understandable without color alone.
- **Motion**: no motion.

### Data Table
- **Structure**: sticky header table inside `.table-wrap`.
- **Variants**: sortable headers, focused rows, outcome status cells.
- **Spacing**: 6px x 10px cell padding.
- **States**: hover row, focused row, sortable header hover.
- **Accessibility**: sorting direction is visible in header text.
- **Motion**: no motion.

### Read-Only Threshold Slider
- **Structure**: `.slider-control` label containing a numeric threshold readout and native range input.
- **Variants**: backtest display filter only; never mutates model thresholds or acceptance metrics.
- **Spacing**: compact inline layout inside `.controls`, full-width row on mobile.
- **States**: native focus ring, live count text beside the trades table.
- **Accessibility**: visible label states that the control filters displayed rows only.
- **Motion**: no motion.

### Signal Detail Tooltip
- **Structure**: fixed `.signal-tooltip` over side-table rows, with title and two-column feature snapshot.
- **Variants**: eligible-but-untraded signal details; anchored by hover or keyboard focus.
- **Spacing**: 10px x 12px padding, 260px width on desktop, bottom sheet style on narrow mobile.
- **States**: hidden, hover, focus.
- **Accessibility**: missed-signal rows are keyboard-focusable and expose the same snapshot on focus.
- **Motion**: no motion.

### Lightweight Chart Panel
- **Structure**: fixed-height `.chart` region powered by Lightweight Charts.
- **Variants**: default, `short`, `mini`, `tall`.
- **Spacing**: chart sits inside panel padding, legends above chart use compact gaps.
- **States**: loading dims the parent view; empty data must render an explicit text state nearby.
- **Accessibility**: surrounding headings and metric tiles summarize the chart.
- **Motion**: chart interactions only.

## 6. Motion & Interaction

### Timing

| Type | Duration | Easing | Usage |
|------|----------|--------|-------|
| Micro | 100ms | ease-out | Hover and active feedback if added |
| Standard | 200ms | ease-in-out | Future panel state transitions |

### Rules
- Current dashboard avoids decorative animation.
- Interactive elements must have hover and active/focused visual states.
- Loading state may dim a view but must not collapse layout.

## 7. Depth & Surface

### Strategy
Mixed, but restrained: tonal-shift plus 1px borders. Shadows are not part of the dashboard language.

| Type | Value | Usage |
|------|-------|-------|
| Panel border | 1px solid var(--border) | Primary analytical containers |
| Tile border | 1px solid var(--border) | Metric surfaces |
| Active fill | var(--surface-2) | Active tabs, active segmented controls, table hover |

Inconsistency to watch: some existing radii are 10px while the broader product guidance prefers 8px or less; preserve current radii for this P1-8 pass and only consolidate with owner-approved visual cleanup.
