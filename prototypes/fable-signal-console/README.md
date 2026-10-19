# Fable Signal Console

本目录是参考线上 Fable Signal Console 制作的本地只读原型，现已接入仓库
最新研究版 L1 + 分侧 L2 模型。

首页默认显示 OKX 15m 实时交互 K 线；可切换到“模型回放”查看实际送入
L1 的同一历史窗口和检测框。实时与回放主图共用同一套深色交互样式；回放
下方仍可展开查看经哈希验证的原始 YOLO 像素输入。两种模式不会混用数据。

## 本地运行

```bash
npm install
npm run dev:full
```

打开 <http://localhost:4173/>。

## 验证

```bash
npm run build
npm run test:sites
```

`dev:full` 同时启动 Vite 页面和本地模型桥。默认模型为：

- L1：Grade-A full40 native-1280，SHA-256 `862705b9…`；
- L2 LONG：`full_110`（110 特征）；
- L2 SHORT：`plus_ma_family`（54 特征）。

页面只从已固化的 holdout 前数据中选择事件，复原同一事件的精确 YOLO
输入并运行两层模型。它不连接交易 API，不下单，不写 `forward_log`，也不
修改 `models/ACTIVE` 或 `models/active_bundle.json`。最新 L2 实验的正式结论
仍是 `REJECT`；这里跑通的是服务链路，不是生产资格或新增绩效证据。

实时图通过 `/api/market/candles/*` 读取最近 300 根 OKX 公共 K 线，再由无需
登录的 `candle15m` WebSocket 更新当前 K 线；断线时退回 15 秒只读轮询。
图中均线与 L1 渲染合同一致，为 SMA/EMA 20、60、120 共六条；均线名称只在
上方图例展示，不在右侧价格轴重复显示。
实时行情只在内存和浏览器中展示，不写 `data/`，不送入 L1/L2，不形成
forward 记录，也不具备执行资格。

回放结果中的 `04:00:00` 是历史事件的 UTC 时间。例如 ETH 当前回放事件为
`2026-04-29 04:00 UTC`，也就是 `2026/04/29 12:00 北京时间`，不是当前检测。
页面现已同时显示日期、UTC 和北京时间。

回放接口同时返回 `replay_chart`：其中 K 线及六条均线来自已经通过像素哈希
校验的同一 pre-holdout 窗口，均线是在完整冻结快照上计算后切片，并非用短
窗口临时重算。检测框从 exact 渲染坐标反算到 K 线 logical/price 坐标，因此
在深色交互图上拖拽、缩放或调整尺寸时仍跟随原 K 线区域。

兼容模型可通过服务端 `FABLE_SIGNAL_CONSOLE_*` 环境变量更换；不兼容的新
协议应新增白名单 adapter，React 页面继续使用稳定的 `/api/model/*` 契约。

图表和行情接口的设计边界见
`docs/decisions/0005-separate-live-market-visualization-from-model-replay.md` 和
`docs/decisions/0006-project-exact-replay-onto-the-interactive-chart.md`。
