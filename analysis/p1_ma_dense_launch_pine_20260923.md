# 均线密集启动：规则移植为 Pine 指标 V1 / V2

日期：2026-09-23。实验：`exp-ma-dense-launch-pine-20260923-v1`。状态：**已交付两个 Pine 文件与 Python 镜像；规则一致性已用冻结候选验证，TradingView 原生编译尚未验证**。

## 结论

- Owner 2026-09-22 问“能写成pine脚本？”并在 2026-09-23 要求“把相似度和评分也移植进去”。V1 只移植两组硬性条件；V2 追加两层相似度与 Grade-A 质量分，与研究扫描一一对应。
- 一致性证据：90 个冻结 Grade-A 候选（3m/5m/15m/30m/1h/4h 各 15 个）在各自确认 K 线上，Python 镜像全部通过两组硬性条件；抽取 6 个候选的质量分与账本记录**最大差 5e-5**（参考数据按 Pine 嵌入的 4 位小数取整后计算），A 级判定与 `quality_tier` 一致。
- V1（仅硬性条件）的信号量约为研究候选的 **10 倍**：AAVE 15m 12 个对 1 个、APT 15m 20 个对 2 个。V2 打开“只显示 A 级”后即回到研究口径。
- 本轮不产生收益结论。之前的收益评估基于严格候选池，不适用于 V1 的宽信号；V2 的 A 级信号与研究候选同口径，但仍未在 TradingView 上逐事件对账。
- 未改 SPIKE 任何版本、监控、仓位或账户；training/production eligible 仍为 false。

## 交付物

| 文件 | 内容 |
|---|---|
| `yoyo/evaluation/pine/ma_dense_launch_v1.pine` | 两组硬性条件（第一道 13 条、第二道 26 条），收拢框、入场/止损/3R 目标、12 小时结果跟踪与统计表 |
| `yoyo/evaluation/pine/ma_dense_launch_v2.template.pine` | V2 模板（含相似度与评分算法，数据用占位符） |
| `yoyo/evaluation/pine/ma_dense_launch_v2.pine` | 由模板与冻结参考数据生成（114 KB；109 行参考数据） |
| `yoyo/evaluation/ma_dense_launch_references.py` | 从两份预注册重建参考数据、常数并渲染 Pine |
| `yoyo/evaluation/ma_dense_launch_v1_reference.py` | Python 镜像：硬性条件、相似度、质量分，供测试对账 |
| `experiments/.../reference_pack.json` | 冻结参考数据包（50 张一阶参考、2 锚定、6 反例、50 家族，4 位小数） |

## 移植的规则

信号出在收拢结束后第 5 根收盘（与训练图右端一致），下一根开盘入场；止损取收拢区最低/最高点；目标 3R；最长 12 小时。均线为收盘价 SMA/EMA 20/60/120，ATR14 用 Wilder 平滑并取收拢结束后第 2 根的值；收拢区 4 根与 5 根都评估，同时成立取 4 根。

1. **第一道（autofill 形态门）**：均线包络 ≤1.5ATR、末根六线宽度 ≤1.1ATR、核心最大实体 ≤1.2ATR、核心推进 ∈[−0.6, 1.3]、确认段第 1/2/3/5 根推进 ≥0/1/1.25/1.75ATR、均线斜率 ≥0.03ATR、收盘最近均线 ≤1.0ATR、收盘出带 ≤1.9ATR、实体出带 ≤1.5ATR。
2. **第一道相似度**：14 个缩放特征 + 4×10 序列，与 50 张 Owner 认可样例取最近，`0.45×特征距离 + 0.55×序列距离 ≤ 0.5`。
3. **第二道（perfect filter 硬门）**：末根宽度 ≤0.95ATR、包络 ≤1.5ATR、核心推进 ∈[−0.6, 1.0]、斜率 ≥0.02ATR/根、收窄或交叉拓扑、K 线触带率 ≥0.4、收盘出带 75 分位 ≤1.5ATR、前 12 根安静四项、核心影线 90 分位 ≤2.0ATR、反向实体 ≤1 根、释放段六项。
4. **第二道评分**：7×22 序列分前段/核心/释放三段，各段先按通道 z 标准化，再算 lock-step、受约束 DTW 与导数 DTW（半径 2，权重 0.35/0.40/0.25），按 0.25/0.35/0.40 合成；对 2 张锚定、6 张反例、50 张家族图取最近（家族先用 lock-step 预筛 3 张）；六轴打分后 `质量分 = 0.75×加权 + 0.25×最弱轴`，门槛 **0.3611898959**，距离尺度 **1.1995844783**。

## 验证

```bash
.venv/bin/python -m pytest -q tests/evaluation/test_ma_dense_launch_v1.py tests/evaluation/test_ma_dense_launch_v2.py
```

12 项通过，包含：阈值逐项对照两份预注册 JSON；Pine 文本携带同样的数值与 ATR 取值位置；生成文件与“模板 + 数据包”重新渲染逐字节相同；镜像的分段距离、lock-step 与最近邻与冻结研究函数一致（1e-12）；核心重采样与 `numpy.interp` 一致；Wilder 平滑递推正确；冻结候选复现其账本质量分与 A 级判定。

补充检查（非测试）：90 个冻结候选全部通过硬性条件；两个 15m 币种上 V1 与研究候选的数量比为 12:1 与 10:1。

## 未移植与限制

- 渲染图片的 `box_height_norm` 门未移植；研究扫描器本身对该字段写 0，不构成差异。
- 家族预筛在距离相同时，研究按 profile 键排序，Pine 按数据包顺序；实测无并列。
- Pine 的 EMA 以首根收盘价起算，与 pandas `adjust=False` 相同；SMA120 需要 120 根预热，图表左端不足时不出信号。
- 研究扫描的跨交易所 4 小时去重无法在单图复现，Pine 改为同方向 4 小时内保留第一个信号。
- 参考数据按 4 位小数嵌入，实测质量分差 ≤5e-5，但不是逐字节相同。
- **TradingView 原生编译与逐事件对账尚未进行**：本轮只有 Python 侧一致性证据，不能声称图表结果与研究完全一致。

## 复现

```bash
cd /Users/zhangzc/fable-trading
.venv/bin/python -m yoyo.evaluation.ma_dense_launch_references \
  --output experiments/active/exp-ma-dense-launch-pine-20260923-v1/reference_pack.json \
  --emit-pine yoyo/evaluation/pine/ma_dense_launch_v2.pine
.venv/bin/python -m pytest -q tests/evaluation/test_ma_dense_launch_v1.py tests/evaluation/test_ma_dense_launch_v2.py
```

参考数据由两份预注册与其钉住的原始行情重建；重跑需要这些冻结输入在位。

## 非方向性说明

本轮是规则移植与一致性验证，没有新的入场、退出、成本或收益主张，因此匹配随机对照、置换检验与 top-decile 收益不适用。等效零假设是逐项对照：任何一处阈值、距离或评分偏离冻结研究实现，测试即失败。

## 下一步（需 Owner 决定）

1. 在 TradingView 编译并保存为私有脚本，然后在若干币上逐事件对账 A 级信号与研究候选。
2. 对账通过后，再讨论是否用 V2 的 A 级信号做新的收益检验；现有收益结论仍是负面的，移植本身不改变它。
