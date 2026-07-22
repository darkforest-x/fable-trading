# 前端可视化优化 — 真落地 + 风格收敛

**日期**：2026-07-22  
**约束**：不杀 v13 / 不 promote / 不耗 holdout / 不动真金 / K 线主图继续 LWC  
**对照**：`analysis/p_wuzao_topics_scan.md` · `src/webapp/static/DESIGN-REFERENCES.md` · wuzao [数据可视化](https://www.wuzao.com/topics/data-visualization/)

---

## 结论先行

第一轮（`4b0c403`）把 Tabulator / 状态灯 / explore 框落地后，Owner 反馈 **整体风格变土**——不是功能错，是视觉像「AI 监控大屏」：6 格状态卡、midnight 表头、seg pill 滤镜、侧栏调试区喧宾夺主。

第二轮按 wuzao 可视化主题里**克制看板**气质收敛：信息保留，权重压低。

| # | 可见点 | 现在的样子 | 打开哪里看 |
|---|--------|------------|------------|
| 1 | 前向日志可排序/筛选 | Tabulator + **自定义 quiet theme**（去 midnight / 少 chip） | `#forward` |
| 2 | train / tip / 新鲜度 | **一行 muted meta**，不再多三张状态卡 | 顶栏三卡下方 |
| 3 | 密集探索框 | LWC + canvas 仍在；图例收紧；overlay 不挡 pan | `#explore` |
| 4 | 调试入口 | 侧栏脚注小链，不占导航分区 | 侧栏底 / `debug_viz.html` |

---

## wuzao 参考（本轮气质，不换栈）

扫自 https://www.wuzao.com/topics/ 与 [数据可视化](https://www.wuzao.com/topics/data-visualization/)；只学密度/层级，不引入喧闹组件库、不 React 重写。

| 参考 | 链接 | 可学的一句 | 刻意没抄 |
|------|------|------------|----------|
| **Grafana** | [wuzao 条目](https://www.wuzao.com/topics/data-visualization/) · [github](https://github.com/grafana/grafana) | 面板顶栏用小号弱对比 meta，不是再堆一排「状态卡」 | 不装 Grafana 栈、不抄霓虹状态灯墙 |
| **Redash** | 同上 · [github](https://github.com/getredash/redash) | 筛选用安静 select/input，表头细线、少圆角胶囊 | 不换 BI 拖拽仪表盘 |
| **Metabase** | 同上 · [github](https://github.com/metabase/metabase) | 字号层级：标签弱、数字才重；表格像文档不像软件皮肤 | 不整站 BI、不问数聊天 UI |
| **FreqUI / Hummingbot**（仓内已记） | [DESIGN-REFERENCES](../src/webapp/static/DESIGN-REFERENCES.md) · wuzao Hummingbot 文档 | 图优先、侧栏分组克制；调试别抢日常导航 | 不 Streamlit 整站 |

---

## 风格问题承认（第一轮）

- 顶栏 3→6 格 + good/warn 渐变卡 = 监控大屏感  
- Tabulator **midnight** 块状表头 + 单元格 chip pill = 表格软件土味  
- 前向「全部/仅新鲜/仅事后」seg 条 = 高对比滤镜条  
- 侧栏「调试」分区 + 密集探索重复入口 = 喧宾夺主  

功能本身（排序、新鲜度筛选、train/tip 只读旁路）可以留；错在**视觉权重**。

---

## 第二轮改了啥

| 文件 | 改动 |
|------|------|
| `index.html` | 恢复 3 状态卡 + `#status-meta`；滤镜改 select；去 midnight CSS；调试沉底脚注；图例收紧 |
| `app.js` | meta 渲染 lag/v13/tip；Tabulator 去 chip、去 midnight class；滤镜绑 select |
| `style.css` | 去掉 6 列；quiet toolbar；Tabulator 贴合 data-table 细线；overlay `pointer-events: none` |
| `vendor/README.md` | 注明 midnight 不用 |

---

## 怎么本地打开预览

```bash
# 勿杀 v13；launchd 已托管则直接刷
open http://127.0.0.1:8642/
# 硬刷新（cache bust: ?v=20260722quiet）
```

看：顶栏仍是三卡，其下有一行淡字 `lag≤30m · v13 … · tip …`；前向表像原表、控件安静；侧栏底才有「调试产物」。

---

## 没做什么及原因

| 项 | 原因 |
|----|------|
| 换 React / Streamlit / Superset | wuzao 扫过但 DESIGN 已否决 |
| ECharts 抢主图 | 主图锁 LWC |
| 删掉 Tabulator / 排序筛选 | 功能有用；只去土味皮肤 |
| 装 Grafana 到 VPS | 运维项需 owner；本轮只学气质 |

---

## 风险与诚实声明

- 本机 forward 表可能仍空（诚实空态）。  
- meta 的 train 仍是 results.csv mtime 启发，不是 pgrep。  
- 未 push / 未动 VPS。  

---

## 下一步（需 Owner）

- [ ] 刷新本机看板：气质是否够干净  
- [ ] 是否 rsync 静态到 VPS（仍不改装机栈）  
- [ ] tip 通后是否单变量叠 ECharts PF 辅图（辅图，不抢 LWC）
