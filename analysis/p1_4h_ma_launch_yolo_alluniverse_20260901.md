# 4h 最新行情 YOLO 全币种扫描（2026-09-01）

## 结论先行

按 Owner 的“扫描所有的币种”指令，本轮在模型推理前冻结了 OKX 当时全部合资格的
**USDT 永续合约 274 个**。其中 273 个有足够的已确认 4h 历史，DOS 只有 132 根，按合同失败
关闭。现有 **Grade-A 8k 正例 + 24k 负例、full40、native-1280** YOLO 原权重与阈值完全不改，
共评分 3,276 张因果窗口图。

统一最新已确认 K 线为北京时间 `2026-09-01 16:00` 开盘、`20:00` 收盘可用。在这个最新端点
仍然实际出框的候选只有 4 个：

| 排名 | 币种 | 方向 | 最新 YOLO conf | 段内峰值 | 读法 |
|---:|---|---|---:|---:|---|
| 1 | **SOONUSDT.P** | **SHORT** | **0.9263** | 0.9263 | 当前最强，但只是检测置信度 |
| 2 | **0GUSDT.P** | **LONG** | **0.7658** | 0.8511 | 连续多端点命中，最新分数低于峰值 |
| 3 | **USELESSUSDT.P** | **LONG** | **0.7086** | 0.8192 | 连续多端点命中，最新分数低于峰值 |
| 4 | **LAUSDT.P** | **LONG** | **0.2682** | 0.7393 | 仅略高于冻结阈值 0.25，明显弱化 |

![当前最新端点的四个 YOLO 候选](output/ma_launch_4h_yolo_alluniverse_20260901_v1/current_signals.png)

最重要的限制：这个 checkpoint **只在 15m 图上训练过**。这次是 Owner 指定的 4h 分布外观察，
不是验证过的 4h 交易模型。confidence 是检测器对图形框的置信度，不是胜率、收益概率或下单授权。

## 当前候选原图

每张图上方是最近 80 根已确认 4h K；彩色矩形是 YOLO 原始四维框投影，虚线 `DETECT` 是模型
输入右端；右下角是模型真正评分的 W18/W19 输入图。绿色为 LONG、红色为 SHORT。图中 K 线时间
是开盘戳；表内“可见”时间已严格加 4 小时到收盘。

### SOONUSDT.P — 当前 SHORT，conf 0.9263

![SOON 4h 当前 SHORT](output/ma_launch_4h_yolo_alluniverse_20260901_v1/charts/01_SOON_SHORT.png)

### 0GUSDT.P — 当前 LONG，conf 0.7658

![0G 4h 当前 LONG](output/ma_launch_4h_yolo_alluniverse_20260901_v1/charts/09_0G_LONG.png)

### USELESSUSDT.P — 当前 LONG，conf 0.7086

![USELESS 4h 当前 LONG](output/ma_launch_4h_yolo_alluniverse_20260901_v1/charts/06_USELESS_LONG.png)

### LAUSDT.P — 当前 LONG，conf 0.2682

![LA 4h 当前 LONG](output/ma_launch_4h_yolo_alluniverse_20260901_v1/charts/13_LA_LONG.png)

## 全部当前与近期候选

“当前”表示最新已确认端点 `20:00 CST` 仍实际出框；“近期”表示过去 24 小时扫描范围内出现过，
但最新端点已不再出框。`最新 conf` 始终取该事件最后一次实际命中端点，不拿历史峰值冒充现值。

| 状态 | 币种 | 方向 | 核心 K 线开盘时间 | 首次收盘可见 | 最后收盘可见 | 最新 conf | 段内峰值 |
|---|---|---|---|---|---|---:|---:|
| 当前 | SOONUSDT.P | SHORT | 08-31 16:00 → 09-01 08:00 | 09-01 20:00 | 09-01 20:00 | 0.9263 | 0.9263 |
| 当前 | 0GUSDT.P | LONG | 08-30 16:00 → 08-31 04:00 | 09-01 00:00 | 09-01 20:00 | 0.7658 | 0.8511 |
| 当前 | USELESSUSDT.P | LONG | 08-31 00:00 → 08-31 16:00 | 09-01 04:00 | 09-01 20:00 | 0.7086 | 0.8192 |
| 当前 | LAUSDT.P | LONG | 08-30 12:00 → 08-31 04:00 | 09-01 00:00 | 09-01 20:00 | 0.2682 | 0.7393 |
| 近期 | WALUSDT.P | LONG | 08-31 12:00 → 09-01 04:00 | 09-01 16:00 | 09-01 16:00 | 0.8010 | 0.8010 |
| 近期 | ZORAUSDT.P | LONG | 08-30 12:00 → 08-31 00:00 | 09-01 00:00 | 09-01 16:00 | 0.6990 | 0.7646 |
| 近期 | ETCUSDT.P | SHORT | 08-30 04:00 → 08-30 20:00 | 09-01 12:00 | 09-01 12:00 | 0.5956 | 0.5956 |
| 近期 | FLOKIUSDT.P | SHORT | 08-30 04:00 → 08-30 20:00 | 09-01 00:00 | 09-01 12:00 | 0.5836 | 0.5836 |
| 近期 | FLOWUSDT.P | SHORT | 08-30 12:00 → 08-31 00:00 | 09-01 04:00 | 09-01 04:00 | 0.3031 | 0.3031 |
| 近期 | RSRUSDT.P | SHORT | 08-30 04:00 → 08-30 20:00 | 09-01 12:00 | 09-01 12:00 | 0.2971 | 0.2971 |
| 近期 | TURBOUSDT.P | SHORT | 08-30 12:00 → 08-31 00:00 | 09-01 16:00 | 09-01 16:00 | 0.2915 | 0.2915 |
| 近期 | ENJUSDT.P | SHORT | 08-30 08:00 → 08-31 00:00 | 09-01 04:00 | 09-01 04:00 | 0.2758 | 0.2758 |
| 近期 | PIPPINUSDT.P | SHORT | 08-31 00:00 → 08-31 12:00 | 09-01 00:00 | 09-01 00:00 | 0.2670 | 0.2670 |
| 近期 | AUCTIONUSDT.P | LONG | 08-29 20:00 → 08-30 08:00 | 09-01 00:00 | 09-01 00:00 | 0.3324 | 0.3324 |

全量图分两页：

![全量候选总览第 1 页](output/ma_launch_4h_yolo_alluniverse_20260901_v1/overview_page_01.png)

![全量候选总览第 2 页](output/ma_launch_4h_yolo_alluniverse_20260901_v1/overview_page_02.png)

## 数据与扫描统计

| 项目 | 数值 |
|---|---:|
| 冻结宇宙 | 274 个 OKX 合资格 USDT 永续合约 |
| 有足够 4h 历史 | 273 个 |
| 历史不足并失败关闭 | DOS：132 根（合同至少 140 根） |
| 冻结 OHLCV 行 | 81,444 |
| 行情范围 | 最早 2026-07-13 16:00 UTC；全部统一止于 2026-09-01 08:00 UTC 开盘 K |
| 扫描端点 | 每币最近 6 个已确认 4h 端点（24 小时） |
| W18/W19 模型输入 | 3,276 |
| 原始 YOLO 框 | 53 |
| 通过 core4/5 + post2–9 结构合同 | 51 |
| 因 post 超合同被拒 | 2 |
| 同币 5-bar 去重后事件 | 14（LONG 6 / SHORT 8） |
| 最新端点仍命中 | 4（LONG 3 / SHORT 1） |
| 扫描墙钟时间 | 238.024 秒 |

“所有币种”的精确定义不是全部现货：本轮冻结的是当时所有 `live`、`instCategory=1`、ticker
有效且价格为正的 OKX 加密资产 USDT 永续，继续执行项目的 `BLOCKED_BASES` 与
`STOCKISH_BASES` 排除。宇宙在任何模型推理之前写入 `universe.json`，没有看到命中后再挑币。

## 冻结模型合同与核验

| 合同 | 固定值 |
|---|---|
| 权重 | `ma_launch_owner_grade_a8000_neg24000_v1_y11s_ft1280_full40/weights/best.pt` |
| 权重 SHA-256 | `862705b999594355c1133640acc540f4de19b561889e89d9e050ddad5c6db838` |
| confidence / NMS | `0.25 / 0.70` |
| 输入 | W18 / W19，原生 1280 |
| 映射核心 | 4 / 5 根 |
| 允许确认 | 2–9 根 |
| 同币事件间隔 | 5 根 |
| 本轮调阈值、改权重、训练 | 0 / 0 / 0 |

离线核验为 **PASS**：273/273 个行情快照 SHA 通过，51/51 个接受候选的模型输入逐像素重渲染
通过，14/14 张交付事件图逐像素重渲染通过；核验期间网络读取 0、模型推理 0。完整回执见
[`verification.json`](output/ma_launch_4h_yolo_alluniverse_20260901_v1/verification.json)。

## 与上一轮雷达扫描对照

| 项目 | 45 币雷达 | 本轮全宇宙 | 变化解释 |
|---|---:|---:|---|
| 冻结币数 | 45 | 274 | Owner 要求扩大覆盖，不是调参 |
| 可用币数 | 42 | 273 | MRNA、CXMT 不在本轮合资格永续宇宙；DOS 仍因历史不足排除 |
| 模型输入 | 504 | 3,276 | 每个可用币仍严格 12 张 |
| 接受框 | 30 | 51 | 新增覆盖带来新增候选；合同未变 |
| 去重事件 | 3 | 14 | 新增 SOON、LA、WAL 等，而非阈值放宽 |
| 当前事件 | 2 | 4 | 原有 0G、USELESS；新增 SOON、LA |

这张表只能说明扫描覆盖扩大后的集合变化，不能解释为模型精度提升。雷达池按当前成交额与涨跌幅
选币，而全宇宙不做 mover 排名；两者不是随机样本，也没有 Gold，不能拿事件率做优劣比较。

## 指标、对照与 holdout 纪律

本轮没有逐样本 Owner 真值、未来收益标签或交易执行，因此 val AUC、置换检验 p、top-decile
毛/净收益、胜率、PF、单特征基线，以及同币 × 同时间块 × 同波动桶匹配随机入场均不适用。
这些是方向策略验收指标；对一份即时图像提案强填数字会制造不存在的经济证据。

对应的严格实现层零假设是：“交付图不是模型实际看到的像素，原框经过二次手画，或市场快照已
漂移。”离线核验从冻结 candles 重建全部 51 张接受输入，按保存的原始 `cx/cy/w/h` 重画全部
14 张交付图，并逐像素、逐文件 SHA 比对；三项全部通过。这个对照只证明交付内部一致，**没有**
拒绝“15m 模型在 4h 上没有语义精度或经济价值”这条更重要的统计零假设。

本轮全部行情晚于 `2026-05-04`。Owner 先明确要求最新 4h YOLO，再明确要求“扫描所有的币种”，
因此记录为该 Grade-A full40 native-1280 checkpoint 的 **holdout 使用 #5**。扫描后没有按结果
调整 confidence、NMS、W、core、post、去重门或宇宙规则。

## 风险与诚实声明

- **分布外是第一风险。** W18/19 在 15m 训练域代表 4.5–4.75 小时，移到 4h 后代表 72–76
  小时；core4/5 变成 16–20 小时，post2–9 变成 8–36 小时。四个“当前”候选也不是刚刚才启动。
- **最新分数不是峰值。** 0G、USELESS、LA 的历史峰值更高；本报告只用最新端点实际分数排序。
  LA 的 0.2682 接近阈值，不能拿它此前 0.7393 的峰值包装成当前强信号。
- **无 Gold、无收益验证。** 14 个事件都是模型提案，未获 Owner 逐样本确认；SOON 的 0.9263
  也不等于 92.63% 胜率。
- **首轮全宇宙也是交互式即时扫描。** scanner 在本次对话中扩展后立即读取市场，builder 没有在
  运行前形成已提交 source commit，因此不能登记为具备提交级谱系的正式实验。冻结宇宙、行情、
  权重与逐像素回放证明当前交付内部一致，但不能倒推扫描前已存在的代码提交。
- 本轮没有训练、promote、ACTIVE/frozen 切换、部署、forward 写入、Telegram 发送或订单动作；
  `production_eligible=false`。

## 下一步选项

1. Owner 可先逐图判断 SOON / 0G / USELESS / LA 是否符合想要的 4h 视觉语义；这是人工审核，
   不等于批准训练或生产。
2. 如果要做真正的 4h YOLO，需要 Owner 另行批准 4h Gold、4h 负样本与时间切分合同；P0/P1
   通过前不能直接拿这 14 个模型提案自动训练或 promote。
3. 如果只想继续观察，可在下一根 4h 收盘后用完全相同合同重扫；每次读取 holdout 都需单独记录。

## 复现命令

以下扫描命令会读取执行时最新的公开行情，因此复现的是同一方法，不会复现本轮已经冻结的市场
截面。精确身份以本轮 `universe.json`、`candles/`、`summary.json` 和 `verification.json` 为准。

```bash
cd /Users/zhangzc/fable-trading

PYTHONPATH=. .venv/bin/python scripts/scan_4h_ma_launch_yolo_latest.py \
  --all-eligible \
  --out analysis/output/ma_launch_4h_yolo_alluniverse_rerun \
  --device mps \
  --batch-size 16 \
  --workers 8

PYTHONPATH=. .venv/bin/python scripts/verify_4h_ma_launch_yolo_scan.py \
  analysis/output/ma_launch_4h_yolo_alluniverse_20260901_v1

python3 scripts/md_to_html.py \
  analysis/p1_4h_ma_launch_yolo_alluniverse_20260901.md \
  --out-dir analysis/html
```
