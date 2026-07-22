# 看板可视化：加深 LWC，不要换主图库

- **问题**：Owner 要「看见前端变好」，而扫描清单里堆满 ECharts / Streamlit / React 交易终端，容易误判成「换栈才能优化」。
- **死胡同**：用 ECharts/TradingView 商业库换掉主 K 线；或只写又一篇 topics 扫描不改页面。主图换库 = 坐标/截图/YOLO 几何全漂；只写文档 = Owner 看不见差异。
- **有效路径**：主图锁定已有 Lightweight Charts；可见杠杆放在 (1) Tabulator 前向表排序筛选 (2) 状态条读本地 train/freshness (3) canvas 密集框 + 调试产物挂载打通。开源进 `static/vendor/`，FastAPI 只挂只读 `/debug-artifacts`。
- **通用规则**：金融看板优化先问「主图是否必须换」——多数时候答否；先加深 markers/overlay/表格/状态灯，再议辅图库。
- **牵连**：`src/webapp/static/app.js`（`drawExploreBoxes`）、`vendor/tabulator*`、`status_strip.py`、`server.py` mount、`analysis/p_frontend_viz_opt.md`；对照 `docs/learnings/chart-overlay-boxes-need-min-size-at-full-zoom.md`。
