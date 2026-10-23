# Pull-to-refresh needs one gesture owner and an awaited data refresh

- **Problem**: A trading App combines vertical page scrolling, chart panning, horizontal navigation and draggable sheets. Adding downward refresh to the same surface can turn one gesture into several actions.
- **Dead end**: Treating any downward movement as refresh ignores the active surface and scroll position. Treating a refresh call as completed when an existing snapshot is still in flight also shows success before data has arrived.
- **Effective path**: Start refresh only at page scroll-top, outside chart/control/sheet areas, and lock the movement axis before consuming the gesture. Give the chart page a small header pull zone. Sheets retain their own controller. A shared refresh event gathers promises from visible data owners; candle requests join the existing snapshot promise instead of returning early.
- **General rule**: First decide which surface owns a gesture. Then make its completion follow the actual asynchronous data operation, including failure and lifecycle cancellation.
- **Related**: `mobile/src/usePullRefresh.ts`, `refreshEvent.ts`, `CandlePanel.tsx`, `candleSession.ts`, `FibHome.tsx`, `Statistics.tsx`; native touch acceptance under `output/playwright/price-line/`.
