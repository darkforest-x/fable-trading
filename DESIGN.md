# Spike — selected Stitch terminal design

Updated: 2026-10-18. This is the implementation contract derived from the **selected exported screen code**, not a claim that Stitch generated this Markdown verbatim.

## Source of truth

Owner selected the dense Obsidian workstation in [Web App UI Redesign](https://stitch.withgoogle.com/projects/8450534110669085172?pli=1). The later cyan project `8978791379095391159` is superseded. Its exported document and screens remain in `docs/design/stitch-20261017/` as historical source, not the active visual specification.

Unmodified source exports are in `docs/design/stitch-20261017/selected-terminal/`:

| Screen | Stitch screen ID | Export |
| --- | --- | --- |
| Full-width market workstation | dcabedc2a7834c798e8d8886d471e5e1 | market-workstation.html |
| Signal and inspection overview | b94aba748c7f4f06b726c8029d3f661c | detection-overview.html |
| Account and execution | 89614c0cbf12460fae8d673e54a9be8b | account-execution.html |
| Order review | 483fbb64f1014384b9a6676cd71270c9 | order-confirmation.html |

The original project's Obsidian document node was still `Generating Document...` without usable content. Do not substitute the separate Quiet Precision / Less, but better document for these selected screens. The exported hierarchy is the layout reference. Owner corrections take precedence over source-specific tokens; the 2026-10-18 correction unifies the market palette with the inspection page.

## Navigation and layout

- The market workstation is the home page. A compact full-width top bar connects market, inspection, execution, research, learning and operations; navigation is organized around tasks rather than the old icon rail.
- Market: full-width selected contract and quote, six source metrics, watchlist / six-MA chart / liquidation and flow columns, then confluence matrix and event feed. Desktop side columns are 238px and 264px; the chart receives remaining space. Panels use small 3–4px radii and thin borders.
- Detection: status strip, search and filters, four-column signal-card matrix, actual account statistics and positions. Clicking a card opens its stored chart; returning preserves the filters. The saved inspection timestamp and causal chart confirmation timestamp are distinct.
- Execution: equity, available funds, floating P&L and 1R, followed by positions, protection, history and order records. The two real accounts stay separate. The result view also contains account statistics and positions.
- Research: process navigation, summary metrics, registry/list with adjacent filters or details. Learning: shelves, topic collections and a readable chapter canvas. Operations: service summary, channel cards and explicit data/error states. These use the same source-derived components; they are not additional exported original screens.
- Mobile ≤700px: horizontal primary navigation, full functional drawer, chart before watchlist, compact three-column metrics, stacked detail panels. Wide tables scroll inside their own containers. No page-wide horizontal overflow.

## Shared theme tokens

Owner correction, 2026-10-18: all workspaces use the inspection page's charcoal surfaces, mint navigation and lavender primary actions. The market keeps its dense workstation layout, but must not redefine global brand or surface tokens on the route. Canvas renderers read the same CSS tokens. Original exports remain unmodified historical source.

| Token | All workspaces |
| --- | --- |
| Canvas / chart | #0b0e14 |
| Panel | #191c22 |
| Inset / card | #1d2026 |
| Hover / selection | #272a31 |
| Main text | #e1e2eb |
| Secondary text | #c7c4d7 |
| Muted | #908fa0 |
| Brand / active navigation | #70ffba |
| Primary action | #c0c1ff |
| Positive | #01e599 |
| Negative | #ff4f73 |

Inter is used for UI text. Market numbers use JetBrains Mono; the other terminal uses the source's Geist family with tabular numbers. Fonts are served locally with `font-display: swap`; upstream OFL licenses accompany them. Chinese uses the system CJK fallback.

All candle views share SMA20 #c0c1ff, SMA60 #00e599, SMA120 #cbd5e1, EMA20 #9d7cff, EMA60 #f59e0b, EMA120 #ff3366. These are **six independent computed series**, not decorative lines.

## Interaction and data contracts

- Every visible action has an actual destination, local state change or existing business action. Secondary explanations use disclosure; repeated actions and decorative fake terminal status are omitted.
- Market scope and ordering cover the whole returned universe, not just visible virtual rows. Search, keyboard navigation, selected instrument and chart period remain usable.
- Shared motion uses short opacity/transform transitions and respects reduced motion. Polling must not replay entrances, reset focus/scroll or recreate charts unnecessarily.
- Chart canvases, forms and real modules stay live. Never paste a screenshot into the application or transplant mock figures from Stitch.
- Public metrics are demand-driven, independently cached and bounded. Failed/expired data display an explicit state. Market display requests do not join the execution worker or inspection pulse.
- Trade flow is the returned sample of at most 500 trades, converted from contracts with instrument ctVal/ctMult. It is not complete-period CVD.
- Liquidations are reported historical events with bankruptcy prices. They are not future liquidation bands or a predictive heatmap. Empty samples and unavailable data are distinct.
- Source example statistics, latency, win rates, safeguards and kill switches grant no authority. Existing confirmation, account isolation, freshness, research and production gates remain unchanged.

## Implementation and verification

Shared: `design-tokens.css`, `fonts/stitch-fonts.css`, `stitch-system.css`, `stitch-pages.css`, `stitch-workspace.js`.
Market: `market-workstation.css`, `market-terminal.js`, `market-intelligence.js`, `yoyo/monitor/market_workstation.py`.
Route coverage and provenance are recorded in `docs/design/stitch-20261017/manifest.json`. Raw browser screenshots and test output are in `output/stitch-redesign-20261017/`.
