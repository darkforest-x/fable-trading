# A review gallery needs rendered-record identity, not only selected-button state

- **问题**：周期筛选的默认首条异步加载可与紧接的手选记录竞争；按钮虽显示后者，旧请求仍可能把前者的图绘进详情。
- **死胡同**：只依赖列表 `.active` 或请求取消，不能证明已开始的旧响应没有进入渲染路径。
- **有效路径**：每次选择递增 epoch 并同时核对记录 ID；真正完成 `draw()` 后才写 `data-rendered-record-id`，供批量截图和 UI 验收比对。
- **通用规则**：异步主详情必须把“当前选择”和“实际渲染对象”分开表达，筛选重新选择也属于竞态来源。
- **牵连**：`yoyo/evaluation/static/spike_v1_review/app.js`、`selection.js`、本地图册批渲染器。
