# Spike — Linear Precision

Owner selected the second displayed concept on 2026-10-17 and authorized a complete UI/UX rebuild of every page. This replaces the rejected NerdWallet treatment.

## Authority and references

- Selected visual target: [owner reattached market terminal](docs/design/references/linear-owner-target.jpg), with the [original concept](docs/design/references/linear-selected.png) retained. The visual language remains a reference; the owner’s later minimalist instruction below supersedes its dense composition.
- Source analysis: [VoltAgent Linear DESIGN.md](https://github.com/VoltAgent/awesome-design-md/blob/3b605329240ce735b510037cca322f1831fdd7f8/design-md/linear.app/DESIGN.md), [local snapshot](docs/design/references/linear.DESIGN.md), [MIT license](docs/design/references/awesome-design-md.LICENSE).
- The source is a community analysis of Linear's marketing design, not an official component library. Adapt its visual language to the real Spike application. The concept contains sample data: never copy sample prices, watchlists, scores or claims into the product.

## Architecture

The active app is `yoyo/monitor/static/` on port 8766; the embedded VLM module is `yoyo/vision_research/static/`. Both participate in the design system.

Use a 70px functional rail and a 240px contextual sidebar for general workspaces; reduce to 64px + 206px between 1024 and 1250px. The selected market terminal uses a 70px rail plus a 280px live watchlist at the 1487px reference viewport; its contextual area contains actual symbols, prices, search and resonance status. Six workspaces: Market, Signals, Accounts, Research, Learning, Operations. Every existing route remains reachable through its contextual menu and global search. Contextual menus list destinations only; do not generate a duplicate page outline. Grouping replaces the long undifferentiated sidebar. Existing per-route filters and reading positions remain navigation-owned.

Four presentation families:
- Terminal: compact toolbar, primary chart or ledger, secondary evidence inspector; charts visible high in the viewport.
- Registry / collection: filter toolbar, flat rows, contextual detail drawer or split editor. Do not bury records beneath large introductory panels.
- Reader: chapter rail and readable document canvas; source, exercises and notes remain accessible.
- Operations: segmented configuration sections and readable status tables, with action feedback beside the control.

The MA detection results workspace pairs the primary chart with a compact live-account inspector: equity, available margin, floating PnL, attributed realized PnL, current positions and recent execution progress. At narrow widths the inspector follows the chart in the same results page. Position names navigate to their charts; protection details disclose per owned lot. Updates retain open details and keyboard focus. Unknown or stale account values must never appear as fresh zeroes. Statistics distinguish unattributed external closes from settled trades.

Manual entry recovers to a fresh review, never to automatic confirmation. Display exchange-read retries separately from inventory conflicts. After confirmation, users can return immediately to detection and follow progress in the inspector. A quote's expiry applies only before confirmation; it must never label an existing position's protection as expired. Order-status polling preserves the existing chart when its price levels and source are unchanged.

## Tokens and components

Dark canvas `#0e1117`, raised `#11151c`, interactive surface `#181d27`, border `#272d38`, text `#eef0f5`, secondary `#c6c9d2`, muted `#a4a8b3`, subtle `#8a8f98`, accent `#939df5`, primary fill `#5e6ad2`. Light mode is a neutral equivalent, not the former forest palette. Market direction colors remain semantic.

Use native SF/PingFang/system sans. Body/control 13–14px, page title 27px (24px mobile), section title 16px, metadata 11–12px. Tabular figures for metrics, prices and tables. Spacing 4/8/12/16/24/32px. Panels 5px radius, controls 5px, 1px boundaries. No atmospheric gradients, glow, oversized heroes or decorative status panels. Use local Tabler icons; preserve the existing chart libraries and their real data.

Shared tokens live in `design-tokens.css`, component families in `design-system.css`, route composition in `workspace-terminal.css`, minimalist composition and menu behavior in `workspace-minimal.css/js`, local tab/disclosure state in `workspace-panels.js`, workspace navigation in `workspace-layouts.js`, and reading state in `reading-tools.js`. Module templates own their structure and state; global styling must not clone form/chart nodes or overwrite trading logic. Lazy-loaded CSS precedes the shared design layer. Shadow DOM receives explicit shared tokens and scoped layout rules.

## Signal Center cards

Owner reaffirmed on 2026-10-17 that Signal Center uses independent cards, not flat registry rows. Use a responsive grid with a 310px minimum card width, 16px gutters and 10px corners. Keep symbol, timeframe, direction, recency, reference price and the three performance facts visible; only supplementary shape/status explanations collapse. Search stays visible above cards; advanced filters expand inline and show active conditions when closed, closed records retain their ledger, and polling preserves each open disclosure. Narrow screens use one card per row.

## Market reference composition

The reference establishes four working regions: navigation/watchlist, quote and chart, evidence inspector, and records. After the owner's detail review on 2026-10-17, the 1487px desktop uses the 350px navigation, 16px main inset, 248px evidence inspector and 14px gutters. The quote panel is now a compact 74px row with symbol, price and 24-hour change; chart height adapts to the viewport. The 1024–1500px layout uses 64px + 254px navigation and a 210px evidence rail, which stacks below at 1200px. At laptop widths the inspector moves below the chart instead of squeezing candles. Panels use 8px corners. These refinements preserve the composition while making the real controls and six-line readout usable.

The market terminal uses the shared canvas, surface, border and text tokens with 8px panel corners. Quotes and symbol title carry the visual hierarchy; the chart is the dominant working surface. The narrow navigation, live watchlist, evidence panel, and tabular records must remain distinct regions. At medium widths the quote toolbar wraps; mobile uses the existing drawer and stacks quote, chart, evidence, and records with bounded table scrolling.

Implementation is owned by `market-terminal.js` / `market-terminal.css`, integrated into the existing `market-sync.js` lifecycle. Candles, levels, quotes and records come from their existing public display endpoints. For 15m / 30m / 1H / 4H, use the scanner's six precomputed, warmed SMA/EMA 20/60/120 columns verbatim. Never copy MA7/30/120 from the illustrative reference or reseed EMA from cropped display candles. Daily bars require all six consecutive closed 4H bars in a UTC day; daily aggregate averages explicitly show missing history when insufficient. Cached, failed and missing data are visibly distinguished. Preserve the bundled TradingView Lightweight Charts attribution. Coin images are local assets from `spothq/cryptocurrency-icons` with the included license in `static/icons/coins/`.

## Interaction contract

- Manual entry shows execution readiness above the detection results. Blocking status names the affected instrument and links to the account; GET-only polling announces recovery without creating or confirming a new order. The ticket places risk, margin and quantity beside the editable settings, with chart/price comparison alongside and a persistent confirmation footer. Mobile uses the same settings-first review order.

- Native keyboard-operable controls and visible focus, selected, disabled, loading, error and empty states.
- Global Cmd/Ctrl+K route search, contextual menu filtering, keyboard rail traversal,  persistent compact/comfortable density.
- Mobile navigation is a modal drawer; tables scroll inside bounded containers and the document never scrolls sideways.
- Detail drawers preserve native dialog focus handling and Escape. Drafts survive switching pages. Keep chart view state and source/freshness labels.
- Existing account identities, manual confirmation, stale-data guards and research/production distinctions stay explicit. No new order, scheduler, deployment, model or data mutation follows from a visual interaction.

## Motion contract

`workspace-motion.css/js` and shared tokens apply to all shell routes and the embedded VLM Shadow DOM. Controls use 120ms feedback, panels 220ms, route/dialog/drawer entry 280ms with a decelerating easing curve. Route transitions fade without translating the view container, so fixed chart overlays retain their containing block. Signal entry staggers at most six cards by 24ms; polling never replays that entrance. User-selected tabs and disclosures animate their existing nodes, preserving form values and chart instances.

Native dialogs retain immediate focus, Escape, close and submission semantics. CSS `display`/`overlay` transitions progressively enhance entry/exit ([MDN pattern](https://developer.mozilla.org/en-US/docs/Web/HTML/Reference/Elements/dialog#animating_dialogs)); closed surfaces stop intercepting pointers immediately. Mobile navigation retains its focus trap and background isolation. No animated price counters, chart drawing, blanket `transition:all`, permanent `will-change`, cloned content, or delayed execution actions. Loading motion means pending work, never fabricated progress.

Respect `prefers-reduced-motion` in CSS (including backdrop/details pseudo-elements) and Web Animations; changing the preference cancels active effects. Cancel owned animations on route changes or hidden documents. Test rapid switching, keyboard focus, polling stability, mobile width and reduced motion in the real browser as well as lifecycle tests.

## Acceptance

Audit all 27 primary routes plus embedded VLM and existing legacy deep links. Exercise every changed interaction with real browser state; compare the actual desktop render and selected reference together at the same viewport, check geometry and component hierarchy, and verify 390px / 1024px layouts. Passing automated tests alone does not prove visual fidelity. Use source-backed content, no sample data. Record necessary screenshots/test outputs, not a separate report. Preserve services and patrol schedules, and close only task-owned test tabs at completion.

## Route composition and progressive disclosure

The selected terminal extends to all 27 primary routes. Registries (datasets, models, factors, experiments, backtests, strategies, YOLO) give records the full primary width. Context and advanced filters use explicit inline disclosures on desktop and phones; the same controls stay mounted and keep their values. Learning, knowledge and content use the same primary-column structure, with editing above the record list. The book shelf uses horizontal book rows, and reader modes prioritize chapter content with progress/source metadata in a remembered disclosure.

Platform and operations use accessible local task tabs, keyboard navigation and stable panels. Manual trading places its workflow explanation in a disclosure above the active task. Paper observations place setup at right, a bounded run list at left and the selected record below. Arthur overview separates processing records from collection/evidence context. VLM keeps scoped Shadow DOM composition and the same shared tokens. Charts, forms, ledger updates and transactional controls remain owned by their existing modules.

Market chart controls toggle actual MA/volume series and reset the visible range. Tick labels prioritize time within a day. The initial/reset range is 100 candles on desktop and 60 on narrow chart canvases; panning and resizing do not force a new view. No illustrative prices, records or invented results are introduced.

## Market detail contract

- All-market means every instrument in the current live USDT perpetual catalog. Rank the full catalog by rolling 24-hour change (descending for gainers, ascending for losers), or USDT volume. Filtering happens before rendering; no arbitrary 40-item truncation. Missing or expired changes sort last, never as zero. Public list quotes arrive in one read-only bulk response and cannot create execution quote receipts.
- The sidebar has one bounded scrolling list; its heading, search, sort and status stay anchored. Polling preserves the visible row and keyboard focus. Sorting or filtering explicitly returns to the first result. Scrollbars remain draggable but thin, neutral and trackless across the application.
- Both market and detection charts expose a persistent six-line key, values synchronized to the crosshair, clear OHLC context and consistent colors from `chart-style.js`. The detection focus includes at least 64 bars of context when available; bounding boxes and detection times are unchanged.
- Chart options, volume, optional key levels, reset and expand work on real chart series. Quote/level/theme refreshes must not reset a user's panned view. Mobile keeps a readable chart rather than making every desktop region narrow.

### 任务密度与可操作细节（2026-10-17）

- 常用控件文字至少 12px，图表辅助标签可为 11px；密度通过合并重复层级调整，不用继续缩字。六均线展示六位有效数字，提示保留源数值，绝不回写计算值。
- 市场周期紧邻图表显示控件。搜索具备清空、Esc 清空与方向键浏览；全市场列表采用单 Tab 入口，轮询保留焦点与滚动锚点。
- 信号保持独立卡片；筛选位于集合上方，窄屏折叠仍显示生效条件。取消过的 TradingView 卡片跳转不恢复，文案必须符合实际行为。
- 均线工作台合并命令栏，币种栏按用户选择展开/收起；小于 1600px 首次使用默认收起，手机沿用币种弹窗。周期只在周期控件展示，常用看图与预览可见，重新检测和跟随行情位于图表设置。
- 窄屏验收同时检查页面宽度及容器内控件边界；零横向溢出不能证明没有内容裁切。
- 高频页面的自动刷新保留操作控件、键盘焦点、展开记录与表格滚动；页签、周期和币种切换后必须核对实际焦点。
- 图表放大使用原生模态窗口并保留原图表节点；显示币种和周期上下文，Esc 退出后回到入口，背景搜索与页面滚动隔离。
- 默认路由明确恢复默认筛选；工作区页签与记录视图可通过浏览器返回。搜索清空只清当前输入，不重置其他筛选。


## 少即是多（Owner，2026-10-17，覆盖早先密集布局）

- 每页一个清楚的主要任务；通过移除重复层级减少密度，不用缩小文字或只换配色。
- 同一功能只保留一个明确入口。市场上方的“查看观察记录”删除；记录就在图表下方。
- 自选 / 全市场与排序共用一行；排序仍覆盖全量目录，保留成交额、涨幅、跌幅。
- 当前币种、价格与涨跌合为紧凑行情行。图表常驻周期、设置和放大；均线、成交量、价位与重置进入设置，六线数值继续显示。
- 正常共振状态只占一行；更新时间、说明与提示音按需展开。异常及数据失效状态不能伪装成正常状态。
- 全站搜索放在功能栏，界面密度、主题与本机状态共用一个菜单。移除自动本页索引、重复面包屑与装饰性英文眉题。
- 高级筛选默认收起，当前搜索和生效条件始终可见。展开必须复用原来的控件、事件和状态，不重建图表或清空表单。
- 菜单支持 Escape 返回触发入口、外部点击关闭、焦点离开关闭。普通设置关闭不触发任何业务写入。
- 交易确认、账户归属、仓位风险、过期数据和执行阻塞仍明确展示；极简不等于隐藏后果。
