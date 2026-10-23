# 4h 最新行情 YOLO 扫描（2026-09-01）

## 结论先行

按 Owner 明确要求，本轮把现有 **Grade-A 8k 正例 + 24k 负例、full40、native-1280** YOLO
原权重直接应用到 4h 图，阈值与结构合同完全不改。扫描到最新已确认 4h K 线（开盘戳
`2026-09-01 16:00 CST`，收盘后 `20:00 CST` 可用）时，仍有 **2 个当前 LONG 候选**：

1. **0GUSDT.P LONG**：最新框 confidence `0.7658`；
2. **USELESSUSDT.P LONG**：最新框 confidence `0.7086`。

另有 **ZORAUSDT.P LONG** 在 `16:00 CST` 前最后一次命中，最新 `20:00 CST` 端点已不再出框，
因此列为近期候选而非当前候选。本轮当前 SHORT 为 0。

![4h YOLO 最新候选总览](output/ma_launch_4h_yolo_latest_20260901_v1/overview.png)

最重要的限制是：这个 checkpoint **只在 15m 图上训练过**。本轮 4h 推理是 Owner 要求的
分布外观察，不是验证过的 4h 模型，更不是交易信号。W18/19 在 15m 原合同中代表
4.5–4.75 小时，换到 4h 后变成 72–76 小时；core4/5 变成 16–20 小时，post2–9 变成
8–36 小时。因此这里看到的是延迟完成形态提案，不能解释为刚刚启动的盘口机会。

## 当前与近期候选

时间均为北京时间。`最新 conf` 是该事件最后一次实际命中窗口的 YOLO confidence；`段内峰值`
单列保留，防止把历史最高分冒充当前分数。confidence 是检测器分数，不是胜率或收益概率。

| 状态 | 币种 | 方向 | 核心 K 线开盘时间 | 首次收盘可见 | 最后收盘可见 | 最新 conf | 段内峰值 |
|---|---|---|---|---|---|---:|---:|
| 当前 | 0GUSDT.P | LONG | 08-30 16:00 → 08-31 04:00 | 09-01 00:00 | 09-01 20:00 | 0.7658 | 0.8511 |
| 当前 | USELESSUSDT.P | LONG | 08-31 00:00 → 08-31 16:00 | 09-01 04:00 | 09-01 20:00 | 0.7086 | 0.8192 |
| 近期 | ZORAUSDT.P | LONG | 08-30 12:00 → 08-31 00:00 | 09-01 00:00 | 09-01 16:00 | 0.6990 | 0.7646 |

### 0GUSDT.P — 当前 LONG

![0G 4h 当前 LONG 原框](output/ma_launch_4h_yolo_latest_20260901_v1/charts/01_0G_LONG.png)

### USELESSUSDT.P — 当前 LONG

![USELESS 4h 当前 LONG 原框](output/ma_launch_4h_yolo_latest_20260901_v1/charts/02_USELESS_LONG.png)

### ZORAUSDT.P — 近期 LONG

![ZORA 4h 近期 LONG 原框](output/ma_launch_4h_yolo_latest_20260901_v1/charts/03_ZORA_LONG.png)

每张图上方为最近 80 根已确认 4h K，绿色矩形是模型真实输出的四维框，虚线 `DETECT` 是该次
模型输入的右端；右下角是模型实际评分的 W18/W19 原生输入。图上的核心时间按 K 线开盘戳记录，
“可见时间”已严格加 4 小时到收盘，不把未完成 K 线当成已知事实。

## 数据与扫描统计

| 项目 | 数值 |
|---|---:|
| 雷达冻结池 | 45 币 |
| 有足够 4h 历史 | 42 币 |
| 历史不足并失败关闭 | 3 币（MRNA 67 根、DOS 132 根、CXMT 86 根） |
| 冻结 OHLCV 行 | 12,457 |
| 每币历史范围 | 最早 2026-07-13 16:00 UTC；统一止于 2026-09-01 08:00 UTC 开盘 K |
| 扫描端点 | 每币最近 6 个已确认 4h 端点（24 小时） |
| W18/W19 实际模型输入 | 504 |
| 原始 YOLO 框 | 32 |
| 通过 core4/5 + post2–9 结构合同 | 30 |
| 因 post 超合同被拒 | 2 |
| 原 5-bar 同币去重后事件 | 3（LONG 3 / SHORT 0） |
| 最新端点仍命中 | 2（LONG 2 / SHORT 0） |

扫描池在模型推理前冻结，沿用现有雷达口径：固定主流币 + 24h USDT 近似成交额 Top10 +
24h 涨幅/跌幅两侧各 Top15（最低约 500 万 USDT），去重后最多 50。本轮实际为 45。该池不是
全市场，而且用当前 24h 涨跌幅选择 mover；因此它适合“给我看现在热币上的模型框”，不适合
估计全市场信号密度或模型精度。

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

独立离线核验结果为 `PASS`：42/42 个行情快照 hash 通过；30/30 个候选的模型输入逐像素重渲染
通过；3/3 张交付图逐像素重渲染通过；核验阶段网络读取 0、模型推理 0。完整回执：
[`verification.json`](output/ma_launch_4h_yolo_latest_20260901_v1/verification.json)。
本轮新增的最新端点、4h 收盘可见时间与画布回归测试，连同既有 4h 合同测试共 `19 passed`。

## 与上一版本对照

此前仓库没有这个 checkpoint 的 4h YOLO 扫描，因此不存在同口径上一版数字。已有 4h Pine
因果规则和 BTC 4h 完成形态相似检索不是同一输入、同一决策时点或同一模型，不能放在结果表里
比较命中率。本轮只建立一份 15m checkpoint → 4h 分布外推理快照，不据此选择下一版参数。

## 指标、对照与 holdout 纪律

本轮没有逐样本 Owner 真值、未来收益标签或交易执行，因此 val AUC、置换检验 p、top-decile
毛/净收益、胜率、PF、单特征基线和同币 × 同时间块 × 同波动桶匹配随机入场均不适用。填这些
数字会把图像提案冒充模型质量或经济证据。

对应的严格零假设是“交付图并非模型实际看到的像素，或绿色框是二次手画”。核验从冻结行情重建
全部 30 个 W18/W19 输入并比对 pixel SHA，再把 3 个代表框按原始 `cx/cy/w/h` 逆投影到全景图；
30/30 输入与 3/3 图均逐像素一致，因此拒绝了这条实现层零假设。它不拒绝“这些 4h 框没有语义
精度或经济价值”这条更重要的统计零假设；后者当前没有 Gold，仍然成立为主要风险。

本轮读取的全部行情均晚于 `2026-05-04`。Owner 先明确要求“4h 周期、最新的数据”，随后再次
明确要求“就用 YOLO 模型”，因此记录为该 Grade-A full40 native-1280 checkpoint 的
**holdout 使用 #4**。扫描后没有按结果调整 threshold、NMS、W、core、post、币池或去重门。

## 风险与诚实声明

- **分布外是第一风险。** 模型学到的 15m 像素/波动/均线几何不保证迁移到 4h；当前三个框可能
  只是尺度巧合。
- **不是新鲜启动。** post2–9 在 4h 上意味着模型必须再看 8–36 小时；框出的核心发生在
  08-30/31，不能包装成 09-01 才出现的入场点。
- **热币选择偏差。** 0G、USELESS、ZORA 都来自当前成交额/涨跌幅雷达，不能把 3/42 当成
  全市场发生率，也不能用事后热度给模型背书。
- **无 Gold、无收益验证。** 三个都是模型提案，不是 Owner 确认正例；confidence 不等于精度。
- **首轮是交互式即时扫描。** scanner 在本次对话中写成后立即读取市场，未满足“builder 先提交、
  再构建”的正式可复现门；因此这份结果只能作为 Owner 即时看图快照，不能登记成具备提交级
  谱系的正式实验。冻结 candles、脚本 SHA 与逐像素回放能证明当前交付内部一致，但不能倒推
  一个扫描前已存在的 source commit。
- 本轮没有训练、promote、ACTIVE/frozen 切换、部署、forward 写入、Telegram 发送或订单动作，
  `production_eligible=false`。

## 下一步选项

1. Owner 只需先看三张图，直接判断 0G / USELESS / ZORA 哪些视觉上算 4h 形态。
2. 若要真正做 4h YOLO，应单独建立 4h Owner Gold、4h 负样本和 4h 时间切分训练合同；不能把
   本轮 15m 权重的三个命中直接拿去自动训练或 promote。
3. 若只想持续观察，可按同一冻结配置下一根 4h 收盘后再扫；任何改阈值或币池都必须另记配置，
   不能与本轮数字混用。

## 复现命令

以下命令会重新读取执行时的最新公开行情，因此复现的是同一冻结方法，不会复现
2026-09-01 这份已经冻结的市场截面。精确数据与图片身份以本轮 `candles/`、`summary.json` 和
`verification.json` 为准。

```bash
cd /Users/zhangzc/fable-trading

PYTHONPATH=. .venv/bin/python scripts/scan_4h_ma_launch_yolo_latest.py \
  --out analysis/output/ma_launch_4h_yolo_latest_rerun \
  --device mps \
  --batch-size 16 \
  --workers 6

python3 scripts/md_to_html.py \
  analysis/p1_4h_ma_launch_yolo_latest_20260901.md \
  --out-dir analysis/html
```
