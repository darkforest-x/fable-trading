# SPIKE 逐币退出复查：原退出 vs 1R 推保本

**结论：逐币结果没有给出可替换 baseline 的稳定 BE 规则。** OKX 1H 最近一年里，V6 的
ZEC 从 +47.15% 变为 +49.18%、USELESS 从 +70.40% 变为 +76.77%，但 ETH V7 从 +13.04%
降至 +6.53%，SOL V6 从 +6.07% 降至 +1.74%，PEPE V6 从 +17.62% 降至 +10.77%。这些是
同一份已看过历史中不同币的相反例子，不能选其中赢家外推为新规则。

这是一份既有、已授权全日期回放的描述性复查，不是新策略评分。1R 价格保本会改变后续
可执行入场，不能把旧交易收益直接替换为 BE 收益；本报告使用原研究已经完成的逐流重放
账本。点名币与固定历史例子均在结果前固定，且不是因果选币回测；即使子集盈利也不能外推
到全币池或未来。图例是后验机制说明，不能估计成功率或证明 alpha。

本次为该切片第 1 次复查已授权的 holdout-era 行；开发/验证均是已经看过的历史研究，
**不是盲样本**。未重跑策略、未调阈值、未抓取行情、未改线上信号/下单/模型。

## 范围、血统与复现

- 冻结来源：`exp-spike-exit-policy-20260912-v1/config.json` 指向的完整 3,531 流 replay；窗口
  2024-09-10 至 2026-09-10，开发/验证切点 2025-09-10。
- 本切片：BTC、ETH、SOL、ZEC、PEPE、WIF、TAO、SOPH、USELESS、BICO、DOGE、SUI；81 个
  交易所×合约×周期流。PEPE 还包括 Binance `1000PEPEUSDT` 的 30m/1H/4H 三流，保留原
  `asset=1000PEPE`、symbol 与未缩放价格，并以 `asset_group=PEPE` 供展示筛选。
- 哈希：输入 config `5708e842…`、上游 frozen manifest `37f65499…`、post manifest
  `e79b027e…`。每一个选中流的 trades/fills（图例还包括 events）均重新核对 completion
  receipt 的 SHA-256。
- 统计：2,187 条事件期间行、2,187 条 1%/1x 独立账户期间行、46,650 笔**模拟**交易、93,300
  条**模拟**成交、31,563 个 baseline/BE 同入场配对；无点名资产缺失。这些笔数含 cohort 和
  policy 的重复路径，既不是实盘开单，也不是独立机会数。

```bash
# 先确认已在 main，且 builder 提交早于这些产物
git branch --show-current
python3 -m pytest -q tests/evaluation/test_spike_coin_be_report.py tests/evaluation/test_spike_coin_be_charts.py
python3 yoyo/evaluation/spike_coin_be_report.py \
  --output experiments/active/exp-spike-coin-be-review-20260912-v1
python3 -m yoyo.evaluation.spike_coin_be_charts \
  --review experiments/active/exp-spike-coin-be-review-20260912-v1
python3 scripts/md_to_html.py analysis/p1_spike_coin_be_review_20260912.md --out-dir analysis/html
```

各维度明细在：

- [全部交易所事件指标](../../experiments/active/exp-spike-coin-be-review-20260912-v1/statistics/coin_event_detail_all_venues.csv)
  （开仓、完成平仓、删失、净胜率、PF、平均/总 R、兑现 ≥10R、MFE ≥10R）。
- [全部交易所独立账户](../../experiments/active/exp-spike-coin-be-review-20260912-v1/statistics/coin_independent_accounts_all_venues.csv)
  （净收益、收盘最大回撤、有效性和杠杆）。
- [全部逐笔交易](../../experiments/active/exp-spike-coin-be-review-20260912-v1/statistics/coin_trade_ledger.csv.gz)、[逐笔成交](../../experiments/active/exp-spike-coin-be-review-20260912-v1/statistics/coin_fill_ledger.csv.gz)、
  [同入场 baseline/BE 配对](../../experiments/active/exp-spike-coin-be-review-20260912-v1/statistics/same_entry_baseline_be_pairs.csv.gz)。

`v1_common_long` 是旧 V1 多头 admission 放入共享退出引擎后的 cohort，不是原生 V1 收益；
`v6_both`/`v7_both` 均为双向 cohort。费用固定为原入场名义金额 0.2% 往返；账户初始资金
10,000、目标初始风险 1%、入场名义不超过权益 1 倍。

## OKX 1H：最近一年独立账户主表

下表是 validation（2025-09-10 至 2026-09-10）每个 OKX 合约自己的账户；`收益/回撤` 为
日历 close NAV，持仓可跨切点延续。它们不能与“按 entry_time 分期”的逐笔事件 R 简单相加。
括号是该期入场数。没有把不同币、不同周期或交易所合成为伪组合。

| 币 | cohort | baseline 收益/回撤（入场） | 1R 价格 BE 收益/回撤（入场） |
|---|---|---:|---:|
| BTC | V1 long | -0.77% / 5.38% (6) | -4.10% / 5.24% (6) |
| BTC | V6 both | -1.92% / 16.16% (81) | -1.75% / 16.81% (85) |
| BTC | V7 both | -0.92% / 10.80% (27) | -4.85% / 13.99% (27) |
| ETH | V1 long | -0.60% / 1.54% (1) | -0.60% / 1.54% (1) |
| ETH | V6 both | -7.65% / 26.17% (82) | -2.82% / 22.97% (85) |
| ETH | V7 both | +13.04% / 11.63% (28) | +6.53% / 10.81% (29) |
| SOL | V1 long | -1.04% / 1.73% (1) | -1.04% / 1.73% (1) |
| SOL | V6 both | +6.07% / 18.00% (77) | +1.74% / 22.09% (82) |
| SOL | V7 both | +2.67% / 9.59% (23) | +2.32% / 10.13% (23) |
| ZEC | V1 long | +9.11% / 3.60% (2) | +7.80% / 3.60% (2) |
| ZEC | V6 both | +47.15% / 6.90% (50) | +49.18% / 6.68% (51) |
| ZEC | V7 both | +35.46% / 5.72% (19) | +39.12% / 4.14% (19) |
| PEPE | V1 long | +0.04% / 3.21% (5) | +0.04% / 3.21% (5) |
| PEPE | V6 both | +17.62% / 17.08% (69) | +10.77% / 23.43% (78) |
| PEPE | V7 both | +3.90% / 7.75% (22) | +1.72% / 10.01% (22) |
| WIF | V1 long | +17.04% / 7.93% (3) | +17.04% / 7.93% (3) |
| WIF | V6 both | -10.14% / 24.87% (78) | -8.76% / 24.48% (84) |
| WIF | V7 both | +2.93% / 14.49% (24) | +3.29% / 14.19% (24) |
| TAO | V1 long | +1.86% / 1.92% (1) | -0.03% / 1.00% (1) |
| TAO | V6 both | -4.81% / 15.48% (81) | -5.63% / 14.73% (83) |
| TAO | V7 both | +2.54% / 10.88% (27) | -1.18% / 12.88% (28) |

固定历史例子也不按收益删去：BICO V6 为 +1.22% / +0.98%，DOGE V6 为 +15.32% / +18.07%，
SOPH V6 为 -11.29% / -9.83%，SUI V6 为 +6.89% / +4.52%，USELESS V6 为 +70.40% / +76.77%
（均为 baseline / 价格 BE 的 validation 账户收益）。相应回撤和每个 cohort/周期见完整 CSV。

## 跨币事件切片（validation，全部交易所）

这是固定币篮子的逐笔结果总和，保留交易所/币/周期明细供审查；它不是独立发现数，也不是
账户组合。`closed` 是实际完成平仓，`censored` 是数据边界/缺口未能确认退出；自然、追踪、
初始止损及反向等具体原因保留在逐笔 trade ledger 的 `exit_reason`。PF 是按每笔原始名义金额
扣费后的净收益 PF，不是复利账户的现金 PF。

| 周期 | cohort | 策略 | 开仓/完成/删失 | 净胜率 | PF | 总 R / 平均 R | 实现 ≥10R |
|---|---|---|---:|---:|---:|---:|---:|
| 30m | V1 long | baseline | 107 / 107 / 0 | 44.86% | 2.99 | 136.23 / 1.273 | 4 |
| 30m | V1 long | 1R price BE | 107 / 107 / 0 | 36.45% | 3.31 | 127.37 / 1.190 | 4 |
| 30m | V6 | baseline | 3628 / 3610 / 18 | 29.58% | 1.01 | 68.18 / 0.019 | 26 |
| 30m | V6 | 1R price BE | 3791 / 3773 / 18 | 24.28% | 1.03 | 44.12 / 0.012 | 26 |
| 30m | V7 | baseline | 1212 / 1200 / 12 | 29.17% | 0.97 | 60.96 / 0.051 | 15 |
| 30m | V7 | 1R price BE | 1235 / 1223 / 12 | 25.27% | 1.03 | 113.22 / 0.093 | 15 |
| 1H | V1 long | baseline | 61 / 61 / 0 | 42.62% | 4.66 | 133.98 / 2.196 | 5 |
| 1H | V1 long | 1R price BE | 61 / 61 / 0 | 32.79% | 4.49 | 121.61 / 1.994 | 5 |
| 1H | V6 | baseline | 1811 / 1801 / 10 | 30.54% | 1.17 | 239.30 / 0.133 | 25 |
| 1H | V6 | 1R price BE | 1910 / 1904 / 6 | 24.95% | 1.20 | 252.50 / 0.133 | 25 |
| 1H | V7 | baseline | 587 / 587 / 0 | 35.26% | 1.74 | 192.59 / 0.328 | 11 |
| 1H | V7 | 1R price BE | 595 / 595 / 0 | 29.58% | 1.79 | 182.16 / 0.306 | 11 |
| 4H | V1 long | baseline | 28 / 28 / 0 | 57.14% | 1.18 | 21.14 / 0.755 | 0 |
| 4H | V1 long | 1R price BE | 28 / 28 / 0 | 57.14% | 2.95 | 24.14 / 0.862 | 0 |
| 4H | V6 | baseline | 571 / 556 / 15 | 36.15% | 1.45 | 118.79 / 0.214 | 7 |
| 4H | V6 | 1R price BE | 605 / 593 / 12 | 30.02% | 1.59 | 138.15 / 0.233 | 7 |
| 4H | V7 | baseline | 210 / 205 / 5 | 32.20% | 1.40 | 76.34 / 0.372 | 6 |
| 4H | V7 | 1R price BE | 216 / 211 / 5 | 26.07% | 1.51 | 80.87 / 0.383 | 6 |

V1 的 30m/1H/4H validation 明细也在同一 CSV；样本分别 107/61/28 个入场，不能因小样本
与共享执行语义而作生产裁决。

## 逐笔图册与配对解释

[图册](../../experiments/active/exp-spike-coin-be-review-20260912-v1/charts/index.html) 含 12 个 validation
后验机制例：BTC、SOL 为“BE 减亏”；ETH、`1000PEPE`、SOPH、SUI 为失败；ZEC、USELESS、BICO
为 baseline 与 BE 都实现至少 10R 的趋势；WIF、TAO、DOGE 为 BE 过早出场。每例都展示冻结
OHLC、六条均线、成交量、IMACD、信号收盘、次开盘入场、初始 SL、1/2/3R、实际 exit 价，以及
engine event ledger 中真实的 `protection_update` 启用时点。图中未来 K 线只为解释已经发生的
历史 exit；不是当时输入。

图册使用本地 TradingView Lightweight Charts v4.2.0，而非 TradingView 官网回测。原站链接仅为
同币同周期页面，不保证定位到历史 K 线。图册 receipt：`charts/chart_receipt.json`，HTML SHA
`09597684…`；12例均通过真实浏览器渲染检查，价格、IMACD和成交量共36个面板均有有效像素，运行错误0。币种筛选12→1→12与三个面板缩放同步已实测。

## 实际浏览器截图与验证

[打开可缩放的 TradingView 图册](http://127.0.0.1:8891/charts/)；12张PNG及逐图记录位于
[浏览器回执](/Users/zhangzc/fable-trading/output/playwright/spike-coin-be-20260912/browser_receipt.json)，
[筛选和同步缩放回执](/Users/zhangzc/fable-trading/output/playwright/spike-coin-be-20260912/interaction_receipt.json)。
以下价格、均线和成交量由 TradingView Lightweight Charts 实际绘制，再由浏览器截图，未使用其他绘图库仿画。

**USELESS / Binance / 1H / V1统一退出，多头：三种退出均兑现25.70R。**
入场0.06751，初始止损0.06174；走势触发保本后没有扫回保护价，最终由后续跟踪保护退出。
图中行情一度达到的43.49R是最大浮盈，不是本笔兑现收益。

![USELESS：保本没有妨碍趋势](/Users/zhangzc/fable-trading/output/playwright/spike-coin-be-20260912/case-09.png)

**BTC / Binance / 1H / V6，空头：保本减少亏损。**
原退出净-1.15R；推至入场价后净-0.15R；按模型费用缓冲的保本约0R。价格保本仍支付费用。

![BTC：保本减少亏损](/Users/zhangzc/fable-trading/output/playwright/spike-coin-be-20260912/case-01.png)

**TAO / Binance / 1H / V6，空头：正常回抽扫掉保本后，走势继续向下。**
原退出兑现5.30R，推至入场价的退出净-0.09R。这是同一笔入场的反事实对照，不是把另一笔大赢家拼进图中。

![TAO：保本过早退出](/Users/zhangzc/fable-trading/output/playwright/spike-coin-be-20260912/case-07.png)

图例是事后选择的机制说明，不能用这12张计算策略成功率。保护止损的触发只精确到所在K线；图上的时刻对应账本K线时间，不能当成逐笔撮合时间。

## 不适用指标、零假设与限制

AUC、top-decile、单特征基线、置换 p 值均不适用：这是固定规则的退出路径比较，没有学习模型
或排序器。本轮同入场 baseline 是严格的退出反事实对照，能回答“同一笔入场改为 BE 后发生什么”；
它**不能**代替同币×时间块×波动桶的随机入场对照，不能证明策略相对随机的 alpha。

主要风险：同一底层在交易所/周期之间重复；未含资金费、冲击、深度、维持保证金/强平；1x 名义
上限使目标 1% 风险可能实际更低；censor 是未知估值而非零收益；所有 validation 早已被看过。
因此没有政策、币种、周期或账户风险被推为生产候选，ACTIVE、Pine、执行器与通知保持不变。

## 下一步

1. 已完成12例浏览器渲染、PNG、筛选和同步缩放验收；完整数值明细与图例保持分离。
2. 若 owner 要选择新的实盘退出规则，必须在新的、未读前向样本上预注册验证；本切片不能作为
   直接替换 baseline 的依据。
