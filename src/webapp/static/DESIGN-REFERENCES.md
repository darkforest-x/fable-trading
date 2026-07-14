# 前端对标项目（开源 / 可参考）

静态 mock：

- **FreqUI 三栏终端**：`docs/design/terminal-mock.html`
- **Hummingbot Dashboard 多页壳**（当前优先试）：`docs/design/hummingbot-mock.html`  
  看板代理：`http://127.0.0.1:8642/hummingbot-mock.html`

## 交易终端 / Bot UI（最相关）

| 项目 | 链接 | Stars 量级 | 学什么 | 不抄什么 |
|------|------|------------|--------|----------|
| **FreqUI** | https://github.com/freqtrade/frequi | ~1k | 三栏终端（币种 / 图 / 表）、少 Tab、图占主屏、markers 标买卖点、结构化 tooltip | Vue 全家桶（可后迁） |
| **Freqtrade 文档截图** | https://www.freqtrade.io/en/stable/freq-ui/ | — | 深色高密度、Plot Configurator、回测工具条 | — |
| **Hummingbot Dashboard** | https://github.com/hummingbot/dashboard | ~350★ | **Streamlit 多页**：侧栏分组、Landing 指标卡、策略页「表单→图→标记→指标→Save」、Instances 卡片、Plotly dark + 买卖三角 | 图交互弱于 TV；全站 Streamlit 与现 FastAPI 双栈成本 |
| **OctoBot** | https://github.com/Drakkar-Software/OctoBot | 中高 | 多交易所 bot 控制台分区 | UI 偏工具站 |
| **Jesse** | https://github.com/jesse-ai/jesse | 中 | 回测结果页信息层级 | 前端非其强项 |
| **OpenCEX** | https://github.com/Polygant/OpenCEX | ~200 | 交易所式专业下单 UI 信息密度 | 撮合/KYC 无关 |
| **HollaEx Kit** | https://github.com/bitholla/hollaex-kit | 中 | 白标交易所 React 壳 | 过重 |
| **ettec/open-trading-platform** | https://github.com/ettec/open-trading-platform | ~170 | React 跨资产执行台布局 | 机构 FIX 栈 |

## 图表与标注（画框/标记）

| 项目 | 链接 | 学什么 |
|------|------|--------|
| **Lightweight Charts** | https://github.com/tradingview/lightweight-charts | 我们已用；官方 demos 的十字线、多 pane |
| **Series Markers** | LWC docs → series-markers | 入场/密集中点用 ▲◆，勿手搓 DOM |
| **Series Primitives** | https://tradingview.github.io/lightweight-charts/docs/plugins/series-primitives | 矩形密集框应走 primitive，跟 pan/zoom 同步 |
| **KLineChart** | https://github.com/klinecharts/KLineChart | 若 LWC 矩形/画线不够可备选（中文社区强） |
| **Charting Library examples** | https://github.com/tradingview/charting-library-examples | 商业库范例；布局参考，许可注意 |

## 金融工作台 / 数据台

| 项目 | 链接 | 学什么 |
|------|------|--------|
| **OpenBB** | https://github.com/OpenBB-finance/OpenBB | 工作区 widget、分析页分离；不把新手教程铺在主图上 |
| **Grafana** | https://github.com/grafana/grafana | 面板密度、时间范围控件、状态灯 |
| **Superset** | https://github.com/apache/superset | 探索 vs 仪表盘分流 |

## 通用后台（只学组件节奏，不当交易范式）

| 项目 | 链接 | 学什么 |
|------|------|--------|
| **shadcn/ui dashboard** | https://ui.shadcn.com/examples/dashboard | 间距、卡片、表格 |
| **Tabler** | https://github.com/tabler/tabler | 表单与空状态 |
| **AdminLTE** | https://github.com/ColorlibHQ/AdminLTE | 侧栏导航模式（可选） |

## 产品对标（闭源，只看交互）

| 产品 | 学什么 |
|------|--------|
| **TradingView** | 图占屏、左侧品种、底部时间轴、绘图工具条 |
| **Binance / OKX 合约页** | 深度与下单；我们不做交易所，只学「图大、控件贴图」 |
| **Bloomberg 终端观感** | 极高密度、键盘优先（我们适度，不做复刻） |

## 和 fable 的映射

| fable 页面 | 对标原型 |
|------------|----------|
| 体验 / 图表 | FreqUI Trade + TV 布局（chart-first） |
| 信号浏览 | FreqUI Chart + 底表成交 |
| 回测 | FreqUI Backtesting 工具条 |
| 前向 / 任务 / 模型 | Hummingbot Dashboard / Grafana 卡片流 |
| 密集框绘制 | LWC Markers（缩略）+ Primitive 矩形（放大） |

## 决策（mock 已体现）

1. **壳**：FreqUI 三栏，不是 Admin 顶栏堆 10 Tab。  
2. **首屏**：图 ≥ 60% 视口；教程一行 tip，可折叠。  
3. **标注**：mock 里用 markers 风格点 + 聚焦矩形，示意目标态（实现阶段再换 primitive）。  
4. **不**整站换 Streamlit / 不搬交易所撮合 UI。
