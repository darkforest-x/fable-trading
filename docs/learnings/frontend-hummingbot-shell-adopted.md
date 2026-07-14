# 前端采用 Hummingbot Dashboard 壳 + 策略页节奏

- **问题**：顶栏 10 Tab + 大 hero 的「说明页」手感，与成熟交易/量化前端差距大；用户选定对标 Hummingbot Dashboard。
- **死胡同**：整站迁 Streamlit 会与现 FastAPI 双栈、丢 LWC 交互；只改颜色不改 IA 仍像运营看板。
- **有效路径**：静态 HTML 仿 HB 壳——左侧分组导航（Main / Config / Orchestration / Data）、Dense Explore 固定为「User inputs → 大图 → scan metrics → 列表」；密集标注改 LWC `setMarkers`（HB triangle-up 风格），聚焦时用 price line 标 hi/lo，去掉易漂的 canvas overlay。
- **通用规则**：量化看板优先「侧栏多页 + 表单驱动图」或「图占主屏三栏」，不要把新手文案堆在图前；标注优先系列原生 markers/primitives。
- **牵连**：`src/webapp/static/{index.html,style.css,app.js}` v=20260714i；mock 仍在 `docs/design/hummingbot-mock.html`。
