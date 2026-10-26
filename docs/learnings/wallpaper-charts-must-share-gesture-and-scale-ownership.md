# Wallpaper charts must share gesture and scale ownership

- **问题**：Patrol looked like the Monday wallpaper but vertical touch panning was disabled, and distant weekly levels compressed the candles into a narrow band.
- **死胡同**：Copying the wallpaper CSS did not copy its interaction contract. The generic embedded-chart options still disabled vertical touch, while the asynchronous overlay forced an automatic scale reset. Making the canvas taller could not fix either cause.
- **有效路径**：Share the Monday wallpaper interaction options and grip-only floating layout. Keep weekly rays visible without including distant levels in the initial candle scale; make full-range fitting an explicit control. Overlay arrival must not reset a user's existing pan. Native touch pan, pinch, panel dragging and range toggling are separate acceptance cases.
- **通用规则**：When two chart surfaces should behave alike, reuse the owner of gestures and scale as well as the renderer. Never let a delayed decoration response reclaim viewport ownership from the user.
- **牵连**：`range-chart.js`, `floating-layout.js`, `yolo-detect.js`, `yolo-chart-levels.js`; focused scale/layout tests and browser touch QA, 2026-10-26. The owner explicitly waived a new Stitch artifact after the edit timed out; existing verified glass designs were reused.
