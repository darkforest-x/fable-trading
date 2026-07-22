# 前端可视化优化 — 真落地（非再扫清单）

**日期**：2026-07-22  
**约束**：不杀 v13 / 不 promote / 不耗 holdout / 不动真金 / K 线主图继续 LWC  
**对照**：`analysis/p_wuzao_topics_scan.md` B11 ECharts（辅图，本轮未上）· A1 LWC 加深 · DESIGN-REFERENCES Tabulator 节奏

---

## 结论先行

Owner 要「看见变好」。本轮在主看板落地 **3 处可见优化**，开源只进 vendor / CDN 风格本地拷贝，**未**换 React、**未**用 ECharts 抢主图。

| # | 可见点 | 开源 | 打开哪里看 |
|---|--------|------|------------|
| 1 | 前向日志可排序/筛选（新鲜·事后·币种） | **Tabulator 6.3**（MIT） | `#forward` 表 |
| 2 | 状态条多三灯：新鲜度门 / v13 训练旁路 / tip_fire | 读本地 `results.csv` + pulse 日志 | 顶栏 |
| 3 | 密集探索：canvas 框 + 起点▲/右缘 tip◆；调试入口打通 hardneg | **LWC v4**（已有）+ 静态产物挂载 | `#explore` · `/debug_viz.html` |

---

## 用了哪些开源

| 库 | 版本/路径 | 用途 | 刻意没用 |
|----|-----------|------|----------|
| TradingView **lightweight-charts** | 已有 `vendor/…standalone…js` | 主 K 线 + markers；加深 canvas 叠框 | 不换 ECharts/TV 商业库做主图 |
| **Tabulator Tables** | `vendor/tabulator.min.{js,css}` + midnight | 前向表排序筛选 | 不整站 AG Grid / React table |
| （对照扫过）uPlot / ECharts / lucide | — | **本轮未 vendor** | ECharts 留给权益辅图（B 档）；lucide 用现有 CSS chip 够 |

另扫确认（WebSearch）：LWC v5 才把 markers 拆成 `createSeriesMarkers`；本仓仍是 **v4 `setMarkers`**，加深路径走 canvas overlay（见 learnings），不升大版本。

---

## 改了哪些文件

| 文件 | 改动 |
|------|------|
| `src/webapp/static/vendor/tabulator*` + `README.md` | 新增本地 vendor |
| `src/webapp/static/index.html` | 状态灯 ×3、侧栏调试链、前向 Tabulator 宿主、探索图例 |
| `src/webapp/static/app.js` | Tabulator 前向表；`drawExploreBoxes` canvas；状态条 train/fresh/tip |
| `src/webapp/static/style.css` | 6 格状态条、Tabulator 深色、overlay 可点 |
| `src/webapp/static/debug_viz.html` | **新**调试入口页 |
| `src/webapp/status_strip.py` | `train` / `freshness` / `tip_pulse` / `debug_links` |
| `src/webapp/server.py` | `mount /debug-artifacts` → `analysis/output` |
| `tests/test_status_strip_train.py` | 只读旁路单测 |
| `analysis/p_frontend_viz_opt.md` | 本报告 |
| `HANDOFF.md` | 一行指针 |
| `docs/learnings/dashboard-viz-deepen-lwc-not-replace.md` | learning |

---

## 怎么本地打开预览

```bash
# 勿杀 v13；用仓库 .venv
cd /Users/zhangzc/fable-trading
PYTHONPATH=. .venv/bin/uvicorn src.webapp.server:app --host 127.0.0.1 --port 8642
```

浏览器：

1. http://127.0.0.1:8642/ — 顶栏应见 **新鲜度 ≤30min**、**v13 训练 epoch x/40**、**tip_fire**（本机无 pulse 日志则 —）
2. 侧栏 **前向** — Tabulator：点表头排序；「仅新鲜 / 仅事后」+ 币种筛选（本机 `forward_log` 若空则空表仍可见控件）
3. 侧栏 **可视化调试** 或 http://127.0.0.1:8642/debug_viz.html — hardneg LWC / tip 对照 / 叠框画廊
4. http://127.0.0.1:8642/#explore — 开「密集标记」：半透明框 + 粉红右缘 tip 带；点框可聚焦

单测：`PYTHONPATH=. python3 -m pytest tests/test_status_strip_train.py -q`

---

## 没做什么及原因

| 项 | 原因 |
|----|------|
| ECharts 权益/PF 辅图 | 前向页已有 LWC area；再叠双库体积，留给 tip 通后 B11 |
| uPlot | 与现有 LWC 辅图重复 ROI 低 |
| React / Streamlit 整站 | DESIGN + wuzao 扫描已否决 |
| 改 VPS / promote / 清 forward_log | 纪律禁止；本机预览即可 |
| 升 LWC v5 | markers API 破坏；本轮加深 overlay 足够看见差异 |
| 假数据灌前向表 | 空表诚实；VPS 有日志后自然满 |

---

## 风险与诚实声明

- 本机 `data/forward_log.csv` 可能只有表头 → 前向表空，但排序/筛选 UI 仍可见。  
- `train.alive` 用 **results.csv / log mtime ≤45min** 启发，不是 `pgrep`；落盘后以 `stable_pt` 为准。  
- `/debug-artifacts` 只读挂载 `analysis/output`；勿把 LWC 截图当 YOLO 训练图。  
- **未**验证 VPS 部署；commit 默认不 push。

---

## 下一步（需 Owner）

- [ ] 是否 rsync 静态到 VPS dashboard（纯前端，仍不改装机栈）  
- [ ] tip 通后是否叠 ECharts 前向 PF 辅图（单变量）  
- [ ] 信号页成交表是否同样换 Tabulator（本轮只做前向，单变量）
