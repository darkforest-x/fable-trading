# Chart overlays must stop before their chart is disposed

- **问题**：Adding range primitives to a reused React candle chart caused `Object is disposed` after navigating away or switching contracts.
- **死胡同**：Disconnecting ResizeObserver and calling chart.remove() looked sufficient. A later effect cleanup still detached the primitive and removed price lines, scheduling another render on the disposed chart.
- **有效路径**：Each overlay cleanup checks that its captured series is still the active graph series. A removed graph owns its own destruction; overlay-only changes still detach normally while the graph remains alive.
- **通用规则**：Treat chart and overlay lifetimes as one ownership boundary. Test unmount, symbol switch and fullscreen transitions, not only the first successful render.
- **牵连**：`yoyo/discipline_app/mobile/src/CandlePanel.tsx`, native Lightweight Charts 5.2 primitives, React effect cleanup order. No execution or risk calculation changes.
