# 全图缩放下 canvas 密集框要有最小可视尺寸

- **问题**：体验页在全窗口（尤其 30 天 / 数千根 15m）画出密集框后，框和 `#id` 标签看起来「标乱了」——位置像对不上、字叠成一团，或框几乎看不见。
- **死胡同**：先怀疑 backend 时间戳/高低价错误，或 canvas 与 chart 有固定像素偏移。诊断页里 `getBoundingClientRect` 对齐误差为 0，且 `t0/t1` 都在 candle times 里，说明不是 API 标错也不是简单 padding 漂移。
- **有效路径**：全图时 `barSpacing` 可到 ~0.5px，5–12 根密集段只有几像素宽，11px 的 `#n` 比框还大，视觉上就像标飞了。修复要点：① overlay 尺寸/原点严格跟 `#explore-chart`（LWC 坐标原点）走；② 用 `timeToCoordinate` + `logicalToCoordinate` 兜底；③ 按 `barSpacing` 半 bar 扩边并设最小宽高；④ 缩略态加强 fill、标签碰撞/按宽度隐藏，放大或 focus 再显示完整 `#id`。
- **通用规则**：凡在 Lightweight Charts 上用 canvas/DOM 叠矩形标注，验收必须覆盖「全图 fitContent」和「单段放大」两档；全图档要有 min box size + 标签降级策略，不能假设 bar 宽永远够写字。
- **牵连**：`src/webapp/static/app.js` 的 `drawExploreBoxes` / `exploreTimeToX`；`style.css` 的 `#explore-overlay`；默认时间窗改为近 7 天减轻首屏拥挤。
