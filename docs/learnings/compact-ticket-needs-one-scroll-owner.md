# A compact ticket needs one scroll owner for entry and management

- **问题**：The mobile quick ticket filled nearly the whole viewport; its fixed action footer consumed the space needed to reach holdings and orders.
- **死胡同**：Reducing only the outer maximum height would preserve the nested form/scroll/footer geometry and leave management below an inaccessible region. Inspection rejected that approach before implementation.
- **有效路径**：Give the sheet 65% of the visual viewport, keep only the heading/mode controls outside the scroller, and place the entry form, summary/action and holdings ledger in one scrolling child. Stop inputs retain a 44px touch target and 16px text, including with the keyboard open.
- **通用规则**：When shrinking a mobile sheet, identify its scroll owner and fixed-height siblings first. Verify actual scroll movement and the input's visible bounds, not just the sheet's CSS height.
- **牵连**：`yoyo/monitor/static/monday-fib.css`, `monday-live.css`, `monday-live.js`, `monday-ledger.js`. Stitch screen `b8b04caa2ba8475a9efab100e295dff9`. Synthetic browser checks at 320/390/1440px verified one scroller; 844px viewport produced a 548.59px sheet, and a 400px keyboard viewport produced a 260px sheet with the input visible. Evidence: `output/playwright/quick-ledger/`.
