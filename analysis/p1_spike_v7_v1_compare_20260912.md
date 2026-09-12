# SPIKE V7 BB 背景准入与归档 V1：结果交付

> **状态：完整 3531 流结果。** 本文只读取已经完成的 replay、post 与 figures 产物；不重跑、不调参、不拉取数据。

## 先看结论

**V7 已加入 BB 压缩背景并保存到 TradingView；全池回测完成，但本轮不能把它认定为全面优于 V1 的加强版。** 它大幅减少 V6 交易，仍比 V1 频繁很多；30 分钟、1 小时扣费后 PF 小于 1。4 小时虽有 PF 大于 1 的结果，匹配随机入场未支持其独立优势。

本次沿用 V1 归档两年池：2024-09-10 至 2026-09-10，Binance／OKX／Gate，30 分钟／1 小时／4 小时，共 3,531 个可比市场周期流。没有把先前小池的 15 分钟研究混入这次同池比较，也不是重新完成三年全市场测试。

### 最直接的同规则比较

下面都使用相同成本、进出场与风险模型；V1 取多头，因此首先比较多头。进场包括期末未结束交易；胜率、PF 只统计已平仓。PF 为净事件收益的盈利总和／亏损绝对值总和，1 是盈亏平衡线，不是账户收益率。

|周期|V1 多头进场|V7 多头进场|V1 净胜率|V7 净胜率|V1 PF|V7 PF|V1 兑现≥10R|V7 兑现≥10R|
|---|---:|---:|---:|---:|---:|---:|---:|---:|
|30 分钟|3,594|32,599|30.31%|28.35%|0.986|0.926|29|267|
|1 小时|2,039|16,967|27.32%|28.81%|1.387|0.869|17|124|
|4 小时|552|5,107|27.12%|30.97%|0.754|1.156|5|53|

V7 多头笔数仍约为 V1 的 8–9 倍。更多 ≥10R 交易伴随更多总交易，不能仅用大赢家数量证明质量提高。归档原生 V1 的 PF 分别为 **0.932／1.279／0.750**；它的执行规则与上述共同执行不同，详细账本单列保留。

|周期|V6 双向进场|V7 双向进场|减少约|V6 PF|V7 PF|V7 净胜率|
|---|---:|---:|---:|---:|---:|---:|
|30 分钟|199,889|65,077|67.4%|0.923|0.981|30.09%|
|1 小时|99,428|32,432|67.4%|0.942|0.934|30.83%|
|4 小时|27,014|9,729|64.0%|1.090|1.117|31.46%|

**减少信号不等于过滤的全是噪音。** 这是本轮最重要的区别。

### 加的逻辑是什么，为什么选它

选固定 B 方案：以 BB200 的相对带宽判断自身历史低位；阈值取此前 500 根带宽的第 10 百分位，信号之前 12 根中须完整出现连续 3 根压缩。只把它作为 V6 新入场的背景门，保留原反向事件的退出权。不加 RSI，不要求启动当根带宽已经扩张，也不增加“预警—确认”图上标签。

B 是根据此前八市场研究选出的较温和方案，目的是避免严苛的当根扩张条件进一步错过起点；**不是从本轮全池里挑出的最优参数**。此前研究已看过部分重叠历史，本轮是固定配置的扩池复核，不是盲测。

主要过滤来自“此前没有压缩”，而非历史长度：30m／1H／4H 原始 V6 事件中，历史不足分别为 2,877／2,906／2,328；历史充分但无近期压缩为 200,909／101,486／24,156。只加相同历史就绪门的 V6 双向 PF 为 0.922／0.942／1.058，对照 V7 的 0.981／0.934／1.117，1H 仍未改善。

V6 本身已放宽原 V1 强劲启动条件，BB 只是低波动背景，无法单独恢复原 V1 同根量价启动的稀疏性。压缩之后也可能继续盘整、假突破或反复换向，这解释了“加了 BB，仍有大量交易”。

### 大趋势保留了多少

V6 双向原来兑现 ≥10R 的交易，V7 在**相同入场根**保留了：30m 315/945、1H 133/403、4H 51/133，约三分之一至四成。V7 自身仍有 349／151／61 笔兑现 ≥10R。

这些不是“整段趋势召回率”：V7 可能换一根 K 线进场，同一行情跨交易所也会重复计数。本轮没有证据支持“保住 80% 正确大行情”，也不把事后最大浮盈 MFE 当作实际止盈。

### 收益是否稳定，是否只是赶上行情

1. **年度不稳定。** V7 多头 4H 的前一年／后一年 PF 为 1.750／0.762，合并看似盈利却掩盖了近期变差。V7 双向 4H 为 1.109／1.124，但仍需下面的对照检验。
2. **匹配随机入场没有给出足够支持。** V7 双向 30m／1H／4H 的配对平均净 R 差为 +0.088／+0.067／−0.155；按月块 sign-flip 的探索性 p 为 0.0187／0.0416／0.9747，均未达到仓库 p<0.01 的标准。4H 实际还弱于匹配随机入场，PF>1 不能归因于这条过滤规则。
3. **V1 的 1H 优势高度集中。** 原生 V1 1H PF=1.279；仅作事后敏感性检查，去掉 RAVE 两笔跨交易所的同一波行情后降为 0.770。共同执行 V1 从 1.387 降至 0.803。原生账本这两笔贡献约 39.8% 的正事件收益。趋势策略依靠少数大赢家是正常特性，但同一次行情不足以证明长期稳定。

上述剔除只用于解释浓度，**正式基准仍保留全部交易**；没有事后移除亏损币、调整参数或宣布新赢家。AUC 与 top-decile 排序毛／净收益不适用：本次没有连续预测得分或分类排序器；以完整规则事件、年度拆分及同币同月同波动桶随机对照检验。

### 回撤与 R 数字必须这样读

V7 双向每个市场周期独立 1x 名义账户中，有已平仓且无无效余额事件的流，其已平仓余额回撤中位数为 **38.23%／34.48%／28.52%**，P90 为 **64.07%／59.97%／55.06%**。有效流中最差分别为 94.04%／92.04%／98.34%。这不是共享资金组合，也不是逐 K 浮动权益最大回撤；不能据此报一个“全市场年化收益”。

4H 双向另有 Binance PIPPIN、TAG 两个流出现单次名义收益低于 −100% 的空头事件，留在逐笔事件统计，但不能继续当作有效复利账户，已单列。未建模合约保证金、强平和资金费，不能直接当作可执行账户回测。

USDC 极小初始风险也会扭曲 R：一个 30m 空头例子最终净名义收益约 −0.1903%，却显示 −82.69R，因为固定 0.2% 成本对应 86.91R。它不是账户损失 82 倍。去掉 USDC 的事后检查并未令 V7 双向 30m 的 PF 突破 1。应同时看百分比收益、成本／风险和 R，不能只看累计 R。

全体 1,143,794 条已平仓账本行（跨执行臂有重复）重算净 R，最大算术差 6.67×10^-11。RAVE 大赢家的价格路径已与冻结输入对应；这确认数据与公式的联结，不代表流动性、资金费和真实成交得到验证。

### 图该怎么看

文后提供 8 张全局 K 线图，标出实际入场、退出与后续走势。其中 6 张来自同一次 RAVE 行情的不同交易所／周期／执行臂，另外 2 张为 USDC 成本异常案例，**不是 8 个独立趋势样本**。它们按事后极端结果选择，适合解释原因，不代表全池成功率；完整逐笔 CSV 才是分母。

### 本轮决定

保留 V7 作为可切换的 BB 背景实验版，不把它自动替换当前监控系统。现有结果没有支持“V7 全面加强 V1”，更没有支持不丢大行情的降噪承诺。

下一轮最值得单独检验的是：以原 V1 的量价启动为基线，加同一 BB 背景门；另开独立实验评估成本相对初始风险过大的标的。每次只改一项，并用未参与选择的新时间段验证。本轮不靠继续调阈值把已知结果修漂亮。

### 审计与可复现附件

- [极端行情／成本敏感性 CSV](/Users/zhangzc/fable-trading/experiments/active/exp-spike-v7-v1-compare-20260912-v1/results/audit_final_v5/post_hoc_sensitivity.csv)
- [无效名义账户事件 CSV](/Users/zhangzc/fable-trading/experiments/active/exp-spike-v7-v1-compare-20260912-v1/results/audit_final_v5/unmodelled_insolvency_events.csv)
- [极值逐笔公式核对 CSV](/Users/zhangzc/fable-trading/experiments/active/exp-spike-v7-v1-compare-20260912-v1/results/audit_final_v5/extreme_example_arithmetic.csv)
- [审计身份与误差回执](/Users/zhangzc/fable-trading/experiments/active/exp-spike-v7-v1-compare-20260912-v1/results/audit_final_v5/audit_manifest.json)

授权与使用记录：Owner 已在本会话允许所有历史日期。本配置在新池的首次完整评估为 v3 全量；之前还有失败的预检与首流 smoke，且 B 选择已用过重叠小池，所以不能称整个历史从未被查看。原生 V1 账本来自既往评估；本次每个共同执行配置记录为第 1 次全池评估，partial/retry 不隐去。post v3 因零交易账本缺列失败，修复后 v4 从相同 raw 完成；audit v4 的原生 MFE 不存在却输出 0 的元数据问题在 v5 移除，正式收益未改。未新增任何训练或生产资格。

附加审计的复现命令（先运行文末 replay/post 命令，再用对应输出）：

```bash
.venv/bin/python -m yoyo.evaluation.spike_v7_v1_audit \
  experiments/active/exp-spike-v7-v1-compare-20260912-v1/results/replay_two_year_20260912_v3 \
  experiments/active/exp-spike-v7-v1-compare-20260912-v1/results/post_final_v4 \
  experiments/active/exp-spike-v7-v1-compare-20260912-v1/results/audit_reproduce
```

## 规则与口径

V7 B 是既定的 V6 原始事件准入背景：先以当前及此前收盘价计算 BB200（中轨为 SMA200，宽度为 `4 × population_std(close, 200) / abs(SMA200)`）；阈值是**前 500 个**宽度的 P10。信号前 12 根都必须已有阈值，且这 12 根中完整出现过连续 3 根压缩；信号根不进入记忆。要求连续历史至少 712 根。没有要求当前带宽扩张，也没有使用 RSI。V7 只过滤新开仓，未过滤的反向 V6 原始事件仍保留退出用途。TradingView 风险框按确认收盘价开启或结束参考；本轮回测按下一根实际开盘价执行，并计入成本。因此图上参考 R 不等于回测净 R，本轮也没有把参考框冒充 TradingView 导出的实际成交账本。

共同执行模型固定为：信号后下一根开盘进场；**含信号根的最近 5 根**极值、0.2 ATR 缓冲、最小 2 ATR 风险；2R 后启动 4 ATR 跟踪；往返成本 0.2%。V1 原版历史账本是**原有只多头执行**，与 V1 共同执行、V6、V7 不能混称为同一策略。V1 共同执行的空头 V6 信号仅作反向平仓；同根冲突以平仓优先。表中 PF 为已平仓**净事件收益**的正收益和除以负收益绝对值和，不是 R 的盈亏比。

## 覆盖与身份

- 冻结可比流：3531；原评估流：3534；当前目录完成：3531。
- 覆盖 Binance / OKX / Gate 的已评估当前目录子集，只含 30m、1H、4H；这不是全部历史上所有币种的池。
- 评估确认窗口：2024-09-10T00:00:00Z 至 2026-09-10T00:00:00Z（不含末点）；指标预热从 2023-08-30T00:00:00Z 开始，仅供历史特征计算。
- 三个 OKX SATS 流（30m/1H/4H）因冻结 catalog tick=0 排除，未用历史 raw tickSz 替代。
- 配置 SHA256：`27f06ae10bf4747771b7d4793ec5deafebc3eaa14637aa1147743de3c2ce138c`；Pine SHA256：`9878a9ddb769be558102e7f99284e80a8562b7eca3fa84999372992f48b6a36c`；replay manifest SHA256：`37f65499611877c8b8b10f0f2fb7a8c99a07d95cc90cda282f5b8a5ac89d41b6`；post manifest SHA256：`b3756f5391e2c94a3f1c8aefcc4470c32e5e9ea8d077a9a8ee11c294bc83e566`。

### replay 源码身份

| 文件 | SHA256 |
| --- | --- |
| yoyo/evaluation/spike_burst_progressive.py | cec81a33997bd39272762bd70a451ceb5bf218da0e7fc0606271d165437fde08 |
| yoyo/evaluation/spike_burst_replay.py | 996ef56ceb5b70da5337422d8390487330f7e387f648ee527b5afc9bda72a33f |
| yoyo/evaluation/spike_burst_v6_structure.py | e84690c02e7b03291cc3037eab37ac495cc2ece79326e24411cfa72aa1214d4b |
| yoyo/evaluation/spike_v1_twoyear_allmarkets.py | 974c86a2d937ec3be80a5ba9002ae9acf381007e4794f2f3b679de2f4127ca9a |
| yoyo/evaluation/spike_v6_bb_squeeze.py | 31f02fdd44f677e227deb6a5560114d2277c206c42c8400ecef3ec864d4046c0 |
| yoyo/evaluation/spike_v6_wvf_study.py | ba8db5027680e259f0c7572e3d31b1fa709481869fd48679e004daec262d3fd4 |
| yoyo/evaluation/spike_v7_fast.py | f0cfe9e2fb6d16a71632728bdfca3f65160f21dbb5afff4d23e61d1acab3bdc0 |
| yoyo/evaluation/spike_v7_v1_compare.py | 7868b180990912b1881f96c10495b48768d0d81a67dcd9ddb9681a3bea2f252f |

## 原版 V1 历史执行（独立参考）

下表来自归档 V1 原版**已覆盖子集**历史账本。完整共同执行完成后才与下方同池口径并列解读。

| 周期(分钟) | 进场 | 已平仓 | 删失 | 胜率 | PF | 平均净R | 真实兑现≥10R |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 30 | 3594 | 3579 | 15 | 29.00% | 0.93 | -0.01 | 30 |
| 60 | 2039 | 2024 | 15 | 26.53% | 1.28 | 0.47 | 18 |
| 240 | 552 | 513 | 39 | 28.46% | 0.75 | -0.09 | 5 |

## 共同执行：多头臂

| 周期(分钟) | 执行臂 | 进场 | 已平仓 | 删失 | 胜率 | PF | 平均净R | 净R合计 | 真实兑现≥10R | MFE≥10R |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 30 | v1_common_execution_long | 3594 | 3590 | 4 | 30.31% | 0.99 | 0.01 | 50.58 | 29 | 84 |
| 60 | v1_common_execution_long | 2039 | 2028 | 11 | 27.32% | 1.39 | 0.52 | 1054.32 | 17 | 48 |
| 240 | v1_common_execution_long | 552 | 520 | 32 | 27.12% | 0.75 | -0.08 | -43.91 | 5 | 20 |
| 30 | v1_common_ready_long | 3557 | 3553 | 4 | 30.42% | 0.99 | 0.02 | 69.28 | 29 | 84 |
| 60 | v1_common_ready_long | 1987 | 1976 | 11 | 27.63% | 1.44 | 0.55 | 1083.74 | 17 | 48 |
| 240 | v1_common_ready_long | 505 | 476 | 29 | 27.73% | 0.78 | -0.07 | -32.22 | 5 | 18 |
| 30 | v6_common_ready_long | 100023 | 99898 | 125 | 27.19% | 0.85 | -0.12 | -12324.21 | 663 | 2199 |
| 60 | v6_common_ready_long | 49503 | 49338 | 165 | 27.43% | 0.89 | -0.08 | -3826.26 | 333 | 1183 |
| 240 | v6_common_ready_long | 13214 | 12842 | 372 | 28.52% | 0.96 | 0.01 | 162.02 | 107 | 413 |
| 30 | v6_unfiltered_long | 101043 | 100916 | 127 | 27.18% | 0.85 | -0.12 | -12473.79 | 671 | 2217 |
| 60 | v6_unfiltered_long | 50536 | 50368 | 168 | 27.39% | 0.88 | -0.08 | -3928.65 | 340 | 1209 |
| 240 | v6_unfiltered_long | 14095 | 13690 | 405 | 28.33% | 1.01 | 0.06 | 756.11 | 123 | 446 |
| 30 | v7_bb_long | 32599 | 32557 | 42 | 28.35% | 0.93 | -0.04 | -1168.15 | 267 | 855 |
| 60 | v7_bb_long | 16967 | 16911 | 56 | 28.81% | 0.87 | -0.06 | -966.40 | 124 | 433 |
| 240 | v7_bb_long | 5107 | 5011 | 96 | 30.97% | 1.16 | 0.13 | 658.20 | 53 | 183 |

`*_common_ready_*` 仅施加与 V7 相同的 BB 历史就绪门，不要求压缩，用于区分冷启动历史限制与压缩准入本身。

## 共同执行：双向臂

| 周期(分钟) | 执行臂 | 进场 | 已平仓 | 删失 | 胜率 | PF | 平均净R | 净R合计 | 真实兑现≥10R | MFE≥10R |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 30 | v6_common_ready_both | 197865 | 197184 | 681 | 29.49% | 0.92 | -0.08 | -15803.09 | 934 | 3626 |
| 60 | v6_common_ready_both | 97413 | 96877 | 536 | 30.45% | 0.94 | -0.06 | -5500.26 | 394 | 1668 |
| 240 | v6_common_ready_both | 25359 | 24846 | 513 | 32.55% | 1.06 | 0.04 | 1108.86 | 116 | 517 |
| 30 | v6_unfiltered_both | 199889 | 199199 | 690 | 29.50% | 0.92 | -0.08 | -15874.36 | 945 | 3650 |
| 60 | v6_unfiltered_both | 99428 | 98881 | 547 | 30.48% | 0.94 | -0.06 | -5445.53 | 403 | 1702 |
| 240 | v6_unfiltered_both | 27014 | 26442 | 572 | 32.37% | 1.09 | 0.07 | 1740.50 | 133 | 551 |
| 30 | v7_bb_both | 65077 | 64816 | 261 | 30.09% | 0.98 | -0.03 | -2226.31 | 349 | 1254 |
| 60 | v7_bb_both | 32432 | 32265 | 167 | 30.83% | 0.93 | -0.05 | -1559.36 | 151 | 619 |
| 240 | v7_bb_both | 9729 | 9610 | 119 | 31.46% | 1.12 | 0.07 | 626.10 | 61 | 240 |

## 共同执行拆分

方向字段 `1` 表示多头，`-1` 表示空头。以下表格是同一已平仓事件指标的年度、交易所和方向拆分；不把不同流复利成全市场组合。

### 按年度完整拆分

| 周期 | 年度段 | 执行臂 | 已平仓 | 胜率 | PF | 平均净R | 兑现≥10R |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 30 | 2024-09_to_2025-09 | v1_common_execution_long | 921 | 30.29% | 0.84 | -0.02 | 4 |
| 30 | 2025-09_to_2026-09 | v1_common_execution_long | 2669 | 30.31% | 1.04 | 0.03 | 25 |
| 60 | 2024-09_to_2025-09 | v1_common_execution_long | 607 | 21.75% | 0.60 | -0.34 | 2 |
| 60 | 2025-09_to_2026-09 | v1_common_execution_long | 1421 | 29.70% | 1.77 | 0.89 | 15 |
| 240 | 2024-09_to_2025-09 | v1_common_execution_long | 140 | 33.57% | 1.65 | 0.32 | 5 |
| 240 | 2025-09_to_2026-09 | v1_common_execution_long | 380 | 24.74% | 0.50 | -0.23 | 0 |
| 30 | 2024-09_to_2025-09 | v1_common_ready_long | 906 | 30.79% | 0.87 | -0.01 | 4 |
| 30 | 2025-09_to_2026-09 | v1_common_ready_long | 2647 | 30.30% | 1.04 | 0.03 | 25 |
| 60 | 2024-09_to_2025-09 | v1_common_ready_long | 592 | 21.96% | 0.60 | -0.34 | 2 |
| 60 | 2025-09_to_2026-09 | v1_common_ready_long | 1384 | 30.06% | 1.85 | 0.93 | 15 |
| 240 | 2024-09_to_2025-09 | v1_common_ready_long | 129 | 33.33% | 1.73 | 0.32 | 5 |
| 240 | 2025-09_to_2026-09 | v1_common_ready_long | 347 | 25.65% | 0.53 | -0.21 | 0 |
| 30 | 2024-09_to_2025-09 | v6_common_ready_both | 75897 | 30.68% | 0.96 | -0.01 | 373 |
| 30 | 2025-09_to_2026-09 | v6_common_ready_both | 121287 | 28.74% | 0.89 | -0.12 | 561 |
| 60 | 2024-09_to_2025-09 | v6_common_ready_both | 37126 | 32.52% | 0.99 | -0.01 | 153 |
| 60 | 2025-09_to_2026-09 | v6_common_ready_both | 59751 | 29.16% | 0.91 | -0.09 | 241 |
| 240 | 2024-09_to_2025-09 | v6_common_ready_both | 10964 | 32.30% | 1.09 | 0.07 | 60 |
| 240 | 2025-09_to_2026-09 | v6_common_ready_both | 13882 | 32.75% | 1.03 | 0.02 | 56 |
| 30 | 2024-09_to_2025-09 | v6_common_ready_long | 38293 | 29.07% | 0.91 | -0.04 | 251 |
| 30 | 2025-09_to_2026-09 | v6_common_ready_long | 61605 | 26.02% | 0.81 | -0.17 | 412 |
| 60 | 2024-09_to_2025-09 | v6_common_ready_long | 18973 | 30.36% | 0.97 | 0.01 | 144 |
| 60 | 2025-09_to_2026-09 | v6_common_ready_long | 30365 | 25.59% | 0.83 | -0.13 | 189 |
| 240 | 2024-09_to_2025-09 | v6_common_ready_long | 5610 | 32.66% | 1.27 | 0.27 | 60 |
| 240 | 2025-09_to_2026-09 | v6_common_ready_long | 7232 | 25.32% | 0.73 | -0.19 | 47 |
| 30 | 2024-09_to_2025-09 | v6_unfiltered_both | 76709 | 30.67% | 0.96 | -0.02 | 378 |
| 30 | 2025-09_to_2026-09 | v6_unfiltered_both | 122490 | 28.76% | 0.90 | -0.12 | 567 |
| 60 | 2024-09_to_2025-09 | v6_unfiltered_both | 37931 | 32.50% | 1.00 | -0.01 | 156 |
| 60 | 2025-09_to_2026-09 | v6_unfiltered_both | 60950 | 29.22% | 0.91 | -0.08 | 247 |
| 240 | 2024-09_to_2025-09 | v6_unfiltered_both | 11605 | 32.37% | 1.12 | 0.09 | 74 |
| 240 | 2025-09_to_2026-09 | v6_unfiltered_both | 14837 | 32.36% | 1.07 | 0.05 | 59 |
| 30 | 2024-09_to_2025-09 | v6_unfiltered_long | 38707 | 29.04% | 0.91 | -0.05 | 255 |
| 30 | 2025-09_to_2026-09 | v6_unfiltered_long | 62209 | 26.03% | 0.81 | -0.17 | 416 |
| 60 | 2024-09_to_2025-09 | v6_unfiltered_long | 19394 | 30.28% | 0.97 | 0.01 | 146 |
| 60 | 2025-09_to_2026-09 | v6_unfiltered_long | 30974 | 25.58% | 0.83 | -0.13 | 194 |
| 240 | 2024-09_to_2025-09 | v6_unfiltered_long | 5969 | 32.59% | 1.30 | 0.29 | 73 |
| 240 | 2025-09_to_2026-09 | v6_unfiltered_long | 7721 | 25.04% | 0.81 | -0.13 | 50 |
| 30 | 2024-09_to_2025-09 | v7_bb_both | 24535 | 32.64% | 1.14 | 0.11 | 143 |
| 30 | 2025-09_to_2026-09 | v7_bb_both | 40281 | 28.54% | 0.88 | -0.12 | 206 |
| 60 | 2024-09_to_2025-09 | v7_bb_both | 12813 | 31.58% | 0.89 | -0.05 | 53 |
| 60 | 2025-09_to_2026-09 | v7_bb_both | 19452 | 30.34% | 0.96 | -0.05 | 98 |
| 240 | 2024-09_to_2025-09 | v7_bb_both | 4116 | 31.49% | 1.11 | 0.11 | 30 |
| 240 | 2025-09_to_2026-09 | v7_bb_both | 5494 | 31.43% | 1.12 | 0.03 | 31 |
| 30 | 2024-09_to_2025-09 | v7_bb_long | 12263 | 30.65% | 1.05 | 0.10 | 108 |
| 30 | 2025-09_to_2026-09 | v7_bb_long | 20294 | 26.96% | 0.85 | -0.12 | 159 |
| 60 | 2024-09_to_2025-09 | v7_bb_long | 6758 | 32.36% | 0.93 | 0.04 | 51 |
| 60 | 2025-09_to_2026-09 | v7_bb_long | 10153 | 26.45% | 0.83 | -0.12 | 73 |
| 240 | 2024-09_to_2025-09 | v7_bb_long | 2088 | 39.27% | 1.75 | 0.57 | 30 |
| 240 | 2025-09_to_2026-09 | v7_bb_long | 2923 | 25.04% | 0.76 | -0.19 | 23 |

### 按交易所完整拆分

| 交易所 | 周期 | 执行臂 | 已平仓 | 胜率 | PF | 平均净R |
| --- | --- | --- | --- | --- | --- | --- |
| binance | 30 | v1_common_execution_long | 2371 | 30.20% | 1.02 | 0.02 |
| binance | 60 | v1_common_execution_long | 1329 | 25.88% | 1.19 | 0.32 |
| binance | 240 | v1_common_execution_long | 298 | 26.51% | 0.83 | -0.08 |
| gate | 30 | v1_common_execution_long | 35 | 28.57% | 0.79 | -0.33 |
| gate | 60 | v1_common_execution_long | 50 | 30.00% | 0.54 | -0.17 |
| gate | 240 | v1_common_execution_long | 86 | 29.07% | 0.45 | -0.08 |
| okx | 30 | v1_common_execution_long | 1184 | 30.57% | 0.90 | 0.01 |
| okx | 60 | v1_common_execution_long | 649 | 30.05% | 2.00 | 0.98 |
| okx | 240 | v1_common_execution_long | 136 | 27.21% | 0.79 | -0.09 |
| binance | 30 | v1_common_ready_long | 2352 | 30.36% | 1.03 | 0.03 |
| binance | 60 | v1_common_ready_long | 1303 | 26.17% | 1.22 | 0.34 |
| binance | 240 | v1_common_ready_long | 272 | 26.84% | 0.87 | -0.06 |
| gate | 30 | v1_common_ready_long | 32 | 28.12% | 0.47 | -0.38 |
| gate | 60 | v1_common_ready_long | 46 | 30.43% | 0.63 | -0.12 |
| gate | 240 | v1_common_ready_long | 85 | 29.41% | 0.45 | -0.07 |
| okx | 30 | v1_common_ready_long | 1169 | 30.62% | 0.90 | 0.01 |
| okx | 60 | v1_common_ready_long | 627 | 30.46% | 2.09 | 1.03 |
| okx | 240 | v1_common_ready_long | 119 | 28.57% | 0.83 | -0.07 |
| binance | 30 | v6_common_ready_both | 129228 | 29.47% | 0.92 | -0.07 |
| binance | 60 | v6_common_ready_both | 63023 | 30.35% | 0.93 | -0.05 |
| binance | 240 | v6_common_ready_both | 12278 | 31.99% | 1.00 | 0.02 |
| gate | 30 | v6_common_ready_both | 1676 | 27.98% | 0.83 | -0.12 |
| gate | 60 | v6_common_ready_both | 2135 | 30.12% | 0.84 | -0.08 |
| gate | 240 | v6_common_ready_both | 6437 | 33.28% | 1.12 | 0.10 |
| okx | 30 | v6_common_ready_both | 66280 | 29.56% | 0.93 | -0.10 |
| okx | 60 | v6_common_ready_both | 31719 | 30.66% | 0.98 | -0.06 |
| okx | 240 | v6_common_ready_both | 6131 | 32.90% | 1.12 | 0.04 |
| binance | 30 | v6_common_ready_long | 65448 | 27.03% | 0.84 | -0.12 |
| binance | 60 | v6_common_ready_long | 32103 | 27.14% | 0.87 | -0.08 |
| binance | 240 | v6_common_ready_long | 6452 | 27.81% | 0.88 | -0.04 |
| gate | 30 | v6_common_ready_long | 860 | 27.21% | 0.89 | -0.07 |
| gate | 60 | v6_common_ready_long | 1136 | 27.46% | 0.77 | -0.16 |
| gate | 240 | v6_common_ready_long | 3207 | 29.44% | 1.04 | 0.10 |
| okx | 30 | v6_common_ready_long | 33590 | 27.51% | 0.85 | -0.14 |
| okx | 60 | v6_common_ready_long | 16099 | 27.98% | 0.93 | -0.06 |
| okx | 240 | v6_common_ready_long | 3183 | 29.06% | 1.04 | 0.02 |
| binance | 30 | v6_unfiltered_both | 130239 | 29.48% | 0.92 | -0.07 |
| binance | 60 | v6_unfiltered_both | 64022 | 30.36% | 0.93 | -0.05 |
| binance | 240 | v6_unfiltered_both | 13208 | 31.92% | 1.05 | 0.06 |
| gate | 30 | v6_unfiltered_both | 1791 | 28.70% | 0.85 | -0.11 |
| gate | 60 | v6_unfiltered_both | 2258 | 30.03% | 0.83 | -0.10 |
| gate | 240 | v6_unfiltered_both | 6477 | 33.19% | 1.13 | 0.10 |
| okx | 30 | v6_unfiltered_both | 67169 | 29.56% | 0.93 | -0.10 |
| okx | 60 | v6_unfiltered_both | 32601 | 30.73% | 0.98 | -0.06 |
| okx | 240 | v6_unfiltered_both | 6757 | 32.44% | 1.16 | 0.04 |
| binance | 30 | v6_unfiltered_long | 65963 | 27.02% | 0.84 | -0.12 |
| binance | 60 | v6_unfiltered_long | 32643 | 27.10% | 0.86 | -0.08 |
| binance | 240 | v6_unfiltered_long | 6954 | 27.71% | 0.96 | 0.04 |
| gate | 30 | v6_unfiltered_long | 923 | 27.30% | 0.87 | -0.09 |
| gate | 60 | v6_unfiltered_long | 1197 | 27.15% | 0.75 | -0.17 |
| gate | 240 | v6_unfiltered_long | 3230 | 29.32% | 1.05 | 0.11 |
| okx | 30 | v6_unfiltered_long | 34030 | 27.51% | 0.86 | -0.14 |
| okx | 60 | v6_unfiltered_long | 16528 | 27.99% | 0.93 | -0.06 |
| okx | 240 | v6_unfiltered_long | 3506 | 28.64% | 1.11 | 0.05 |
| binance | 30 | v7_bb_both | 42076 | 30.00% | 0.98 | -0.03 |
| binance | 60 | v7_bb_both | 20818 | 30.56% | 0.93 | -0.04 |
| binance | 240 | v7_bb_both | 4780 | 30.79% | 1.02 | 0.02 |
| gate | 30 | v7_bb_both | 607 | 32.13% | 1.12 | 0.10 |
| gate | 60 | v7_bb_both | 733 | 31.38% | 0.88 | -0.03 |
| gate | 240 | v7_bb_both | 2440 | 32.34% | 1.25 | 0.16 |
| okx | 30 | v7_bb_both | 22133 | 30.21% | 0.98 | -0.06 |
| okx | 60 | v7_bb_both | 10714 | 31.30% | 0.95 | -0.06 |
| okx | 240 | v7_bb_both | 2390 | 31.88% | 1.21 | 0.06 |
| binance | 30 | v7_bb_long | 21108 | 27.99% | 0.91 | -0.04 |
| binance | 60 | v7_bb_long | 10924 | 28.41% | 0.86 | -0.05 |
| binance | 240 | v7_bb_long | 2532 | 29.94% | 1.00 | 0.04 |
| gate | 30 | v7_bb_long | 309 | 33.01% | 1.35 | 0.28 |
| gate | 60 | v7_bb_long | 388 | 27.06% | 0.70 | -0.19 |
| gate | 240 | v7_bb_long | 1234 | 32.41% | 1.39 | 0.29 |
| okx | 30 | v7_bb_long | 11140 | 28.90% | 0.95 | -0.05 |
| okx | 60 | v7_bb_long | 5599 | 29.72% | 0.90 | -0.06 |
| okx | 240 | v7_bb_long | 1245 | 31.65% | 1.30 | 0.15 |

### 按方向完整拆分

| 周期 | 方向 | 执行臂 | 已平仓 | 胜率 | PF | 平均净R |
| --- | --- | --- | --- | --- | --- | --- |
| 30 | 1.0 | v1_common_execution_long | 3590 | 30.31% | 0.99 | 0.01 |
| 60 | 1.0 | v1_common_execution_long | 2028 | 27.32% | 1.39 | 0.52 |
| 240 | 1.0 | v1_common_execution_long | 520 | 27.12% | 0.75 | -0.08 |
| 30 | 1.0 | v1_common_ready_long | 3553 | 30.42% | 0.99 | 0.02 |
| 60 | 1.0 | v1_common_ready_long | 1976 | 27.63% | 1.44 | 0.55 |
| 240 | 1.0 | v1_common_ready_long | 476 | 27.73% | 0.78 | -0.07 |
| 30 | -1.0 | v6_common_ready_both | 97286 | 31.84% | 1.01 | -0.04 |
| 30 | 1.0 | v6_common_ready_both | 99898 | 27.19% | 0.85 | -0.12 |
| 60 | -1.0 | v6_common_ready_both | 47539 | 33.59% | 1.01 | -0.04 |
| 60 | 1.0 | v6_common_ready_both | 49338 | 27.43% | 0.89 | -0.08 |
| 240 | -1.0 | v6_common_ready_both | 12004 | 36.85% | 1.19 | 0.08 |
| 240 | 1.0 | v6_common_ready_both | 12842 | 28.52% | 0.96 | 0.01 |
| 30 | 1.0 | v6_common_ready_long | 99898 | 27.19% | 0.85 | -0.12 |
| 60 | 1.0 | v6_common_ready_long | 49338 | 27.43% | 0.89 | -0.08 |
| 240 | 1.0 | v6_common_ready_long | 12842 | 28.52% | 0.96 | 0.01 |
| 30 | -1.0 | v6_unfiltered_both | 98283 | 31.87% | 1.01 | -0.03 |
| 30 | 1.0 | v6_unfiltered_both | 100916 | 27.18% | 0.85 | -0.12 |
| 60 | -1.0 | v6_unfiltered_both | 48513 | 33.68% | 1.01 | -0.03 |
| 60 | 1.0 | v6_unfiltered_both | 50368 | 27.39% | 0.88 | -0.08 |
| 240 | -1.0 | v6_unfiltered_both | 12752 | 36.70% | 1.19 | 0.08 |
| 240 | 1.0 | v6_unfiltered_both | 13690 | 28.33% | 1.01 | 0.06 |
| 30 | 1.0 | v6_unfiltered_long | 100916 | 27.18% | 0.85 | -0.12 |
| 60 | 1.0 | v6_unfiltered_long | 50368 | 27.39% | 0.88 | -0.08 |
| 240 | 1.0 | v6_unfiltered_long | 13690 | 28.33% | 1.01 | 0.06 |
| 30 | -1.0 | v7_bb_both | 32259 | 31.85% | 1.04 | -0.03 |
| 30 | 1.0 | v7_bb_both | 32557 | 28.35% | 0.93 | -0.04 |
| 60 | -1.0 | v7_bb_both | 15354 | 33.05% | 1.01 | -0.04 |
| 60 | 1.0 | v7_bb_both | 16911 | 28.81% | 0.87 | -0.06 |
| 240 | -1.0 | v7_bb_both | 4599 | 31.99% | 1.07 | -0.01 |
| 240 | 1.0 | v7_bb_both | 5011 | 30.97% | 1.16 | 0.13 |
| 30 | 1.0 | v7_bb_long | 32557 | 28.35% | 0.93 | -0.04 |
| 60 | 1.0 | v7_bb_long | 16911 | 28.81% | 0.87 | -0.06 |
| 240 | 1.0 | v7_bb_long | 5011 | 30.97% | 1.16 | 0.13 |

## 信号、准入与尾部逐笔留存

V7 拒绝原因是先验门，而不是事后收益筛选。`insufficient_bb_history` 表示 712 根连续历史不足；`ready_without_recent_compression` 表示 BB 历史充分但此前 12 根没有完整 3 根压缩。

| 周期 | 方向 | 原始V6 | 历史不足 | 就绪但无压缩 | V7准入 |
| --- | --- | --- | --- | --- | --- |
| 30 | -1 | 141525 | 1469 | 100370 | 39686 |
| 30 | 1 | 142349 | 1408 | 100539 | 40402 |
| 60 | -1 | 72876 | 1444 | 52259 | 19173 |
| 60 | 1 | 72094 | 1462 | 49227 | 21405 |
| 240 | -1 | 18599 | 1080 | 11990 | 5529 |
| 240 | 1 | 19812 | 1248 | 12166 | 6398 |

### 各臂候选与准入

| 执行臂 | 周期 | 方向 | 候选 | 准入 | 仅作反向退出的空头原始事件 |
| --- | --- | --- | --- | --- | --- |
| v1_common_execution_long | 30 | -1 | 0 | 0 | 141525 |
| v1_common_execution_long | 30 | 1 | 3594 | 3594 | 0 |
| v1_common_execution_long | 60 | -1 | 0 | 0 | 72876 |
| v1_common_execution_long | 60 | 1 | 2039 | 2039 | 0 |
| v1_common_execution_long | 240 | -1 | 0 | 0 | 18599 |
| v1_common_execution_long | 240 | 1 | 552 | 552 | 0 |
| v1_common_ready_long | 30 | -1 | 0 | 0 | 141525 |
| v1_common_ready_long | 30 | 1 | 3594 | 3557 | 0 |
| v1_common_ready_long | 60 | -1 | 0 | 0 | 72876 |
| v1_common_ready_long | 60 | 1 | 2039 | 1987 | 0 |
| v1_common_ready_long | 240 | -1 | 0 | 0 | 18599 |
| v1_common_ready_long | 240 | 1 | 552 | 505 | 0 |
| v6_common_ready_both | 30 | -1 | 141525 | 140056 | 0 |
| v6_common_ready_both | 30 | 1 | 142349 | 140941 | 0 |
| v6_common_ready_both | 60 | -1 | 72876 | 71432 | 0 |
| v6_common_ready_both | 60 | 1 | 72094 | 70632 | 0 |
| v6_common_ready_both | 240 | -1 | 18599 | 17519 | 0 |
| v6_common_ready_both | 240 | 1 | 19812 | 18564 | 0 |
| v6_common_ready_long | 30 | -1 | 141525 | 0 | 0 |
| v6_common_ready_long | 30 | 1 | 142349 | 140941 | 0 |
| v6_common_ready_long | 60 | -1 | 72876 | 0 | 0 |
| v6_common_ready_long | 60 | 1 | 72094 | 70632 | 0 |
| v6_common_ready_long | 240 | -1 | 18599 | 0 | 0 |
| v6_common_ready_long | 240 | 1 | 19812 | 18564 | 0 |
| v6_unfiltered_both | 30 | -1 | 141525 | 141525 | 0 |
| v6_unfiltered_both | 30 | 1 | 142349 | 142349 | 0 |
| v6_unfiltered_both | 60 | -1 | 72876 | 72876 | 0 |
| v6_unfiltered_both | 60 | 1 | 72094 | 72094 | 0 |
| v6_unfiltered_both | 240 | -1 | 18599 | 18599 | 0 |
| v6_unfiltered_both | 240 | 1 | 19812 | 19812 | 0 |
| v6_unfiltered_long | 30 | -1 | 141525 | 0 | 0 |
| v6_unfiltered_long | 30 | 1 | 142349 | 142349 | 0 |
| v6_unfiltered_long | 60 | -1 | 72876 | 0 | 0 |
| v6_unfiltered_long | 60 | 1 | 72094 | 72094 | 0 |
| v6_unfiltered_long | 240 | -1 | 18599 | 0 | 0 |
| v6_unfiltered_long | 240 | 1 | 19812 | 19812 | 0 |
| v7_bb_both | 30 | -1 | 141525 | 39686 | 0 |
| v7_bb_both | 30 | 1 | 142349 | 40402 | 0 |
| v7_bb_both | 60 | -1 | 72876 | 19173 | 0 |
| v7_bb_both | 60 | 1 | 72094 | 21405 | 0 |
| v7_bb_both | 240 | -1 | 18599 | 5529 | 0 |
| v7_bb_both | 240 | 1 | 19812 | 6398 | 0 |
| v7_bb_long | 30 | -1 | 141525 | 0 | 0 |
| v7_bb_long | 30 | 1 | 142349 | 40402 | 0 |
| v7_bb_long | 60 | -1 | 72876 | 0 | 0 |
| v7_bb_long | 60 | 1 | 72094 | 21405 | 0 |
| v7_bb_long | 240 | -1 | 18599 | 0 | 0 |
| v7_bb_long | 240 | 1 | 19812 | 6398 | 0 |

逐笔同 entry identity 的真实兑现 ≥10R 留存如下；这是已平仓交易的精确联结，未把 MFE 当兑现。这里的“同 entry 未留存”只表示 V7 没有在该 V6 进场根实际进场，**不等于整段行情漏掉**：V7 仍可能在另一根进场。本轮没有计算行情段级召回率；请同时查看 V7 自身的“真实兑现≥10R”笔数。

| 基线 | V7目标 | 周期 | 基线已平仓 | 基线兑现≥10R | 同 entry 留存 | 同 entry 未留存 |
| --- | --- | --- | --- | --- | --- | --- |
| v1_common_execution_long | v7_bb_long | 30 | 3590 | 29 | 6 | 23 |
| v1_common_execution_long | v7_bb_long | 60 | 2028 | 17 | 0 | 17 |
| v1_common_execution_long | v7_bb_long | 240 | 520 | 5 | 0 | 5 |
| v1_common_ready_long | v7_bb_long | 30 | 3553 | 29 | 6 | 23 |
| v1_common_ready_long | v7_bb_long | 60 | 1976 | 17 | 0 | 17 |
| v1_common_ready_long | v7_bb_long | 240 | 476 | 5 | 0 | 5 |
| v6_unfiltered_long | v7_bb_long | 30 | 100916 | 671 | 241 | 430 |
| v6_unfiltered_long | v7_bb_long | 60 | 50368 | 340 | 109 | 231 |
| v6_unfiltered_long | v7_bb_long | 240 | 13690 | 123 | 43 | 80 |
| v6_common_ready_long | v7_bb_long | 30 | 99898 | 663 | 242 | 421 |
| v6_common_ready_long | v7_bb_long | 60 | 49338 | 333 | 109 | 224 |
| v6_common_ready_long | v7_bb_long | 240 | 12842 | 107 | 44 | 63 |
| v6_unfiltered_both | v7_bb_both | 30 | 199199 | 945 | 315 | 630 |
| v6_unfiltered_both | v7_bb_both | 60 | 98881 | 403 | 133 | 270 |
| v6_unfiltered_both | v7_bb_both | 240 | 26442 | 133 | 51 | 82 |
| v6_common_ready_both | v7_bb_both | 30 | 197184 | 934 | 316 | 618 |
| v6_common_ready_both | v7_bb_both | 60 | 96877 | 394 | 133 | 261 |
| v6_common_ready_both | v7_bb_both | 240 | 24846 | 116 | 52 | 64 |

### V1 同根冲突的退出优先

当前 post 产物没有 `v1_conflict_counts.csv`：这不等于冲突为零，只表示该聚合文件未产出。原版 V1 历史账本始终未改。

## 单币独立 1x 已平仓余额与回撤

每个市场/周期/segment 单独按 1x notional 已平仓净收益复利；没有跨币、跨交易所或跨流资金曲线。主表的收益/DD 中位与 P90 **只取有已平仓交易且无破产/无效收益事件的流**，因此同时报告总流数、有已平仓交易流、无已平仓交易流、零进场流、仅删失流和异常流，避免样本分母被静默改变后误读回撤。

| 周期(分钟) | 执行臂 | 总流数 | 有已平仓交易流 | 无已平仓交易流 | 零进场流 | 仅删失流 | 破产/异常流 | 中位/P90样本 | 余额收益中位 | 余额收益P90 | 余额DD中位 | 余额DD P90 | 最差单流余额DD |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 30 | v1_common_execution_long | 1144 | 958 | 186 | 186 | 0 | 0 | 958 | -4.49% | 17.96% | 9.11% | 23.83% | 53.87% |
| 60 | v1_common_execution_long | 1126 | 849 | 277 | 275 | 2 | 0 | 849 | -6.07% | 15.76% | 9.37% | 24.89% | 66.89% |
| 240 | v1_common_execution_long | 1261 | 414 | 847 | 823 | 24 | 0 | 414 | -10.17% | 27.38% | 10.86% | 30.11% | 77.74% |
| 30 | v1_common_ready_long | 1144 | 951 | 193 | 193 | 0 | 0 | 951 | -4.49% | 18.13% | 9.02% | 23.80% | 53.87% |
| 60 | v1_common_ready_long | 1126 | 830 | 296 | 294 | 2 | 0 | 830 | -5.98% | 16.22% | 9.25% | 23.77% | 63.93% |
| 240 | v1_common_ready_long | 1261 | 381 | 880 | 859 | 21 | 0 | 381 | -10.01% | 27.49% | 10.61% | 28.91% | 77.74% |
| 30 | v6_common_ready_both | 1144 | 1106 | 38 | 36 | 2 | 1 | 1105 | -51.78% | 30.11% | 71.61% | 91.84% | 99.55% |
| 60 | v6_common_ready_both | 1126 | 1095 | 31 | 31 | 0 | 5 | 1090 | -40.83% | 35.99% | 66.30% | 89.34% | 99.84% |
| 240 | v6_common_ready_both | 1261 | 1110 | 151 | 139 | 12 | 8 | 1102 | -18.31% | 92.83% | 51.68% | 80.93% | 99.33% |
| 30 | v6_common_ready_long | 1144 | 1106 | 38 | 38 | 0 | 0 | 1106 | -44.76% | 17.73% | 64.94% | 85.22% | 97.63% |
| 60 | v6_common_ready_long | 1126 | 1086 | 40 | 35 | 5 | 0 | 1086 | -36.49% | 24.94% | 59.52% | 82.14% | 96.69% |
| 240 | v6_common_ready_long | 1261 | 1094 | 167 | 155 | 12 | 0 | 1094 | -23.59% | 58.89% | 45.25% | 70.55% | 95.43% |
| 30 | v6_unfiltered_both | 1144 | 1116 | 28 | 25 | 3 | 1 | 1115 | -50.30% | 28.54% | 71.87% | 92.09% | 99.55% |
| 60 | v6_unfiltered_both | 1126 | 1108 | 18 | 15 | 3 | 5 | 1103 | -41.39% | 35.56% | 66.66% | 89.65% | 99.88% |
| 240 | v6_unfiltered_both | 1261 | 1205 | 56 | 37 | 19 | 8 | 1197 | -17.88% | 95.40% | 51.96% | 81.40% | 99.45% |
| 30 | v6_unfiltered_long | 1144 | 1112 | 32 | 31 | 1 | 0 | 1112 | -45.80% | 17.66% | 65.30% | 85.30% | 97.63% |
| 60 | v6_unfiltered_long | 1126 | 1105 | 21 | 19 | 2 | 0 | 1105 | -38.18% | 21.84% | 59.94% | 82.95% | 96.69% |
| 240 | v6_unfiltered_long | 1261 | 1180 | 81 | 63 | 18 | 0 | 1180 | -23.59% | 59.67% | 45.54% | 71.84% | 95.90% |
| 30 | v7_bb_both | 1144 | 1105 | 39 | 38 | 1 | 0 | 1105 | -10.41% | 49.37% | 38.23% | 64.07% | 94.04% |
| 60 | v7_bb_both | 1126 | 1086 | 40 | 37 | 3 | 0 | 1086 | -11.25% | 42.00% | 34.48% | 59.97% | 92.04% |
| 240 | v7_bb_both | 1261 | 1071 | 190 | 178 | 12 | 2 | 1069 | -8.76% | 61.58% | 28.52% | 55.06% | 98.34% |
| 30 | v7_bb_long | 1144 | 1099 | 45 | 44 | 1 | 0 | 1099 | -11.30% | 28.41% | 29.86% | 51.25% | 81.53% |
| 60 | v7_bb_long | 1126 | 1075 | 51 | 47 | 4 | 0 | 1075 | -11.50% | 29.57% | 26.52% | 48.06% | 80.86% |
| 240 | v7_bb_long | 1261 | 1041 | 220 | 208 | 12 | 0 | 1041 | -8.10% | 49.63% | 19.55% | 42.64% | 73.94% |

### 含零交易流的附表

这里先排除破产/无效收益流，再把其余无已平仓交易流的余额收益与 DD 记为 0；表中分母单列，仅用于展示无已平仓交易流对分布的影响，不能与主表混读。

| 周期 | 执行臂 | 非异常分母 | 余额收益中位（含零） | 余额收益P90（含零） | DD中位（含零） | DD P90（含零） |
| --- | --- | --- | --- | --- | --- | --- |
| 30 | v1_common_execution_long | 1144 | -2.04% | 15.07% | 7.43% | 22.55% |
| 60 | v1_common_execution_long | 1126 | -2.06% | 10.98% | 5.81% | 21.98% |
| 240 | v1_common_execution_long | 1261 | 0.00% | 0.00% | 0.00% | 16.87% |
| 30 | v1_common_ready_long | 1144 | -1.96% | 15.07% | 7.32% | 22.15% |
| 60 | v1_common_ready_long | 1126 | -1.52% | 10.84% | 5.52% | 21.24% |
| 240 | v1_common_ready_long | 1261 | 0.00% | 0.00% | 0.00% | 16.30% |
| 30 | v6_common_ready_both | 1143 | -49.00% | 26.99% | 70.41% | 91.79% |
| 60 | v6_common_ready_both | 1121 | -38.18% | 35.21% | 65.52% | 89.05% |
| 240 | v6_common_ready_both | 1253 | -10.01% | 84.09% | 46.85% | 79.41% |
| 30 | v6_common_ready_long | 1144 | -42.24% | 16.88% | 63.87% | 84.98% |
| 60 | v6_common_ready_long | 1126 | -34.30% | 23.34% | 57.87% | 81.88% |
| 240 | v6_common_ready_long | 1261 | -16.48% | 47.60% | 40.05% | 69.28% |
| 30 | v6_unfiltered_both | 1143 | -48.35% | 25.91% | 70.76% | 92.01% |
| 60 | v6_unfiltered_both | 1121 | -40.74% | 35.19% | 66.25% | 89.62% |
| 240 | v6_unfiltered_both | 1253 | -14.16% | 91.27% | 50.80% | 81.10% |
| 30 | v6_unfiltered_long | 1144 | -43.67% | 16.58% | 64.15% | 85.20% |
| 60 | v6_unfiltered_long | 1126 | -36.40% | 21.61% | 59.52% | 82.70% |
| 240 | v6_unfiltered_long | 1261 | -19.70% | 53.74% | 43.51% | 70.68% |
| 30 | v7_bb_both | 1144 | -9.06% | 47.90% | 37.46% | 63.74% |
| 60 | v7_bb_both | 1126 | -10.38% | 40.27% | 33.26% | 59.35% |
| 240 | v7_bb_both | 1259 | -1.49% | 55.22% | 24.62% | 52.47% |
| 30 | v7_bb_long | 1144 | -9.63% | 26.64% | 29.17% | 50.90% |
| 60 | v7_bb_long | 1126 | -10.09% | 27.04% | 25.27% | 47.83% |
| 240 | v7_bb_long | 1261 | -1.99% | 37.74% | 15.98% | 40.45% |

原版 V1 独立账户汇总在 [`historical_v1_native_independent_account_summary.csv`](/Users/zhangzc/fable-trading/experiments/active/exp-spike-v7-v1-compare-20260912-v1/results/delivery_final_v4/historical_v1_native_independent_account_summary.csv)；它遵循上方原版 V1 的同一范围警告。

## 匹配随机对照

每个已实现样本在读取结果前按 entry identity SHA256 选取，每个流/臂/方向至多 16 笔；匹配同 venue+symbol、周期、日历月、因果前 120 根波动分位与方向，seed=0。共同 ready / V7 臂的随机候选还须满足同一 BB 历史就绪门。配对差是事件层，不是共享资本组合收益；本研究复用既往审阅过的数据，**不是盲测**。少于六个月 block 时不报告 p 值。

| 周期 | 执行臂 | 预定样本 | 成功配对 | 月block | 配对平均净R差 | 等权月平均R差 | 探索性sign-flip p |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 30 | v1_common_execution_long | 3590 | 3584 | 25 | 0.13 | 0.10 | 0.1382 |
| 60 | v1_common_execution_long | 2028 | 2026 | 25 | 0.14 | 0.02 | 0.4222 |
| 240 | v1_common_execution_long | 520 | 514 | 25 | -0.47 | -0.35 | 0.9861 |
| 30 | v1_common_ready_long | 3553 | 3547 | 25 | 0.14 | 0.10 | 0.1335 |
| 60 | v1_common_ready_long | 1976 | 1974 | 25 | 0.18 | 0.06 | 0.3237 |
| 240 | v1_common_ready_long | 476 | 470 | 25 | -0.49 | -0.34 | 0.9820 |
| 30 | v6_common_ready_both | 33707 | 33595 | 25 | 0.08 | 0.09 | 0.0261 |
| 60 | v6_common_ready_both | 31205 | 31094 | 25 | -0.01 | -0.01 | 0.6116 |
| 240 | v6_common_ready_both | 23090 | 22887 | 25 | -0.15 | -0.16 | 0.9999 |
| 30 | v6_common_ready_long | 16879 | 16836 | 25 | 0.06 | 0.09 | 0.0863 |
| 60 | v6_common_ready_long | 15644 | 15595 | 25 | -0.05 | -0.07 | 0.9558 |
| 240 | v6_common_ready_long | 11937 | 11780 | 25 | -0.27 | -0.29 | 1.0000 |
| 30 | v6_unfiltered_both | 34084 | 33971 | 25 | 0.08 | 0.08 | 0.0247 |
| 60 | v6_unfiltered_both | 31827 | 31714 | 25 | -0.01 | -0.01 | 0.5982 |
| 240 | v6_unfiltered_both | 24620 | 24407 | 25 | -0.15 | -0.17 | 1.0000 |
| 30 | v6_unfiltered_long | 17076 | 17034 | 25 | 0.06 | 0.08 | 0.0944 |
| 60 | v6_unfiltered_long | 15929 | 15880 | 25 | -0.05 | -0.07 | 0.9505 |
| 240 | v6_unfiltered_long | 12732 | 12565 | 25 | -0.27 | -0.29 | 1.0000 |
| 30 | v7_bb_both | 29934 | 29868 | 25 | 0.09 | 0.10 | 0.0187 |
| 60 | v7_bb_both | 24931 | 24871 | 25 | 0.07 | 0.09 | 0.0416 |
| 240 | v7_bb_both | 9610 | 9529 | 25 | -0.15 | -0.12 | 0.9747 |
| 30 | v7_bb_long | 14984 | 14967 | 25 | 0.11 | 0.11 | 0.0826 |
| 60 | v7_bb_long | 12758 | 12731 | 25 | 0.07 | 0.08 | 0.1268 |
| 240 | v7_bb_long | 5011 | 4945 | 25 | -0.27 | -0.22 | 0.9924 |

AUC 不适用：这里没有分类器概率或排序模型，只有规则事件与成对随机入场对照。

## 图例与逐笔账本

![V1 common-execution winner：okx RAVE-USDT-SWAP 60m](/Users/zhangzc/fable-trading/experiments/active/exp-spike-v7-v1-compare-20260912-v1/results/figures_final_v4/01_okx_RAVE-USDT-SWAP_60m.png)

[01_okx_RAVE-USDT-SWAP_60m.png](/Users/zhangzc/fable-trading/experiments/active/exp-spike-v7-v1-compare-20260912-v1/results/figures_final_v4/01_okx_RAVE-USDT-SWAP_60m.png)：V1 common-execution winner；okx RAVE-USDT-SWAP 60m，多，仅供事后审阅，含未来 K 线。

![V1 common-execution winner：binance RAVEUSDT 60m](/Users/zhangzc/fable-trading/experiments/active/exp-spike-v7-v1-compare-20260912-v1/results/figures_final_v4/02_binance_RAVEUSDT_60m.png)

[02_binance_RAVEUSDT_60m.png](/Users/zhangzc/fable-trading/experiments/active/exp-spike-v7-v1-compare-20260912-v1/results/figures_final_v4/02_binance_RAVEUSDT_60m.png)：V1 common-execution winner；binance RAVEUSDT 60m，多，仅供事后审阅，含未来 K 线。

![V7 realized winner：binance RAVEUSDT 30m](/Users/zhangzc/fable-trading/experiments/active/exp-spike-v7-v1-compare-20260912-v1/results/figures_final_v4/03_binance_RAVEUSDT_30m.png)

[03_binance_RAVEUSDT_30m.png](/Users/zhangzc/fable-trading/experiments/active/exp-spike-v7-v1-compare-20260912-v1/results/figures_final_v4/03_binance_RAVEUSDT_30m.png)：V7 realized winner；binance RAVEUSDT 30m，多，仅供事后审阅，含未来 K 线。

![V7 realized winner：okx RAVE-USDT-SWAP 30m](/Users/zhangzc/fable-trading/experiments/active/exp-spike-v7-v1-compare-20260912-v1/results/figures_final_v4/04_okx_RAVE-USDT-SWAP_30m.png)

[04_okx_RAVE-USDT-SWAP_30m.png](/Users/zhangzc/fable-trading/experiments/active/exp-spike-v7-v1-compare-20260912-v1/results/figures_final_v4/04_okx_RAVE-USDT-SWAP_30m.png)：V7 realized winner；okx RAVE-USDT-SWAP 30m，多，仅供事后审阅，含未来 K 线。

![V7 realized loss：binance USDCUSDT 30m](/Users/zhangzc/fable-trading/experiments/active/exp-spike-v7-v1-compare-20260912-v1/results/figures_final_v4/05_binance_USDCUSDT_30m.png)

[05_binance_USDCUSDT_30m.png](/Users/zhangzc/fable-trading/experiments/active/exp-spike-v7-v1-compare-20260912-v1/results/figures_final_v4/05_binance_USDCUSDT_30m.png)：V7 realized loss；binance USDCUSDT 30m，空，仅供事后审阅，含未来 K 线。

![V7 realized loss：okx USDC-USDT-SWAP 30m](/Users/zhangzc/fable-trading/experiments/active/exp-spike-v7-v1-compare-20260912-v1/results/figures_final_v4/06_okx_USDC-USDT-SWAP_30m.png)

[06_okx_USDC-USDT-SWAP_30m.png](/Users/zhangzc/fable-trading/experiments/active/exp-spike-v7-v1-compare-20260912-v1/results/figures_final_v4/06_okx_USDC-USDT-SWAP_30m.png)：V7 realized loss；okx USDC-USDT-SWAP 30m，多，仅供事后审阅，含未来 K 线。

![V6 entry not retained by V7：okx RAVE-USDT-SWAP 60m](/Users/zhangzc/fable-trading/experiments/active/exp-spike-v7-v1-compare-20260912-v1/results/figures_final_v4/07_okx_RAVE-USDT-SWAP_60m.png)

[07_okx_RAVE-USDT-SWAP_60m.png](/Users/zhangzc/fable-trading/experiments/active/exp-spike-v7-v1-compare-20260912-v1/results/figures_final_v4/07_okx_RAVE-USDT-SWAP_60m.png)：V6 entry not retained by V7；okx RAVE-USDT-SWAP 60m，多，仅供事后审阅，含未来 K 线。

![V6 entry not retained by V7：binance RAVEUSDT 60m](/Users/zhangzc/fable-trading/experiments/active/exp-spike-v7-v1-compare-20260912-v1/results/figures_final_v4/08_binance_RAVEUSDT_60m.png)

[08_binance_RAVEUSDT_60m.png](/Users/zhangzc/fable-trading/experiments/active/exp-spike-v7-v1-compare-20260912-v1/results/figures_final_v4/08_binance_RAVEUSDT_60m.png)：V6 entry not retained by V7；binance RAVEUSDT 60m，多，仅供事后审阅，含未来 K 线。

- [共同执行逐笔账本 CSV](/Users/zhangzc/fable-trading/experiments/active/exp-spike-v7-v1-compare-20260912-v1/results/post_final_v4/common_execution_trades.csv.gz)
- [匹配随机样本 CSV](/Users/zhangzc/fable-trading/experiments/active/exp-spike-v7-v1-compare-20260912-v1/results/post_final_v4/sampled_matched_controls.csv.gz)
- [信号/准入统计 CSV](/Users/zhangzc/fable-trading/experiments/active/exp-spike-v7-v1-compare-20260912-v1/results/post_final_v4/signal_counts.csv)
- [独立账户汇总 CSV](/Users/zhangzc/fable-trading/experiments/active/exp-spike-v7-v1-compare-20260912-v1/results/delivery_final_v4/independent_account_summary.csv)

## 风险与诚实声明

- V7 B 是固定规则，未在本次输出后重新调参；此前已查看的重叠数据不构成新的盲测。
- 只读取指定已覆盖子集；没有补数据、填缺口、变更目录或以当前赢家筛选资产。
- BB 门需要完整前史 712 根；成本固定为往返 0.2%，未建模 funding、冲击、滑点差异或交易容量。
- 未平仓交易标为删失，不参与已平仓胜率、PF、净R或独立余额；不能以 MFE 替代真实兑现。
- PF 与事件净R是单笔描述；独立余额/DD 也只在单流内计算。本文没有全市场组合收益。
- 历程包含失败尝试：`f02fbf0` 的预检因 SATS tick=0 未产生新交易输出；`aaa2767`/v2 在首流产生部分交易后于汇总失败；`d88205a`/v3 先完成 smoke 再续跑全池。v3 只复用 v2 已校验的输入清单，未复用其交易产物；因此不能称这批数据首次被查看或为盲测。
- 完整覆盖也只能评价这个预注册的历史配置，不能自动 promote、训练或进入生产。

## 复现

```bash
TASK_PYTHON=.venv/bin/python
REPLAY=/Users/zhangzc/fable-trading/experiments/active/exp-spike-v7-v1-compare-20260912-v1/results/replay_two_year_20260912_final
POST=/Users/zhangzc/fable-trading/experiments/active/exp-spike-v7-v1-compare-20260912-v1/results/post_two_year_20260912_final
FIGURES=/Users/zhangzc/fable-trading/experiments/active/exp-spike-v7-v1-compare-20260912-v1/results/figures_two_year_20260912_final
DELIVERY=/Users/zhangzc/fable-trading/experiments/active/exp-spike-v7-v1-compare-20260912-v1/results/delivery_two_year_20260912_final

$TASK_PYTHON -m yoyo.evaluation.spike_v7_v1_compare "$REPLAY"
$TASK_PYTHON -m yoyo.evaluation.spike_v7_v1_report "$REPLAY" "$POST" --controls
$TASK_PYTHON -m yoyo.evaluation.spike_v7_v1_figures "$REPLAY" "$POST" "$FIGURES"
$TASK_PYTHON -m yoyo.evaluation.spike_v7_v1_delivery --raw "$REPLAY" --post "$POST" --figures "$FIGURES" \
  --report /Users/zhangzc/fable-trading/analysis/p1_spike_v7_v1_compare_20260912.md --delivery-dir "$DELIVERY" --interpretation /Users/zhangzc/fable-trading/experiments/active/exp-spike-v7-v1-compare-20260912-v1/review/interpretation.md
$TASK_PYTHON scripts/md_to_html.py --out-dir analysis/html /Users/zhangzc/fable-trading/analysis/p1_spike_v7_v1_compare_20260912.md
```

输入目录：[`raw`](/Users/zhangzc/fable-trading/experiments/active/exp-spike-v7-v1-compare-20260912-v1/results/replay_two_year_20260912_v3)、[`post`](/Users/zhangzc/fable-trading/experiments/active/exp-spike-v7-v1-compare-20260912-v1/results/post_final_v4)、[`figures`](/Users/zhangzc/fable-trading/experiments/active/exp-spike-v7-v1-compare-20260912-v1/results/figures_final_v4)。报告 builder SHA256 在 delivery manifest 中记录；所有表格数字直接读取上述产物。
