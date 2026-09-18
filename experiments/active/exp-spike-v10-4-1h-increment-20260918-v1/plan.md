# plan · exp-spike-v10-4-1h-increment-20260918-v1 · RUN_ID v104-1h-incr-20260918-01

写于任何新计算之前（2026-09-18）。**这 20 个月已经看过**：六周期报告（ccc52dff29 / 9e98a0068e）
和 1h 拆解报告（8a86d36f05）已读过 1h 的 v9_long 与 joint 全部逐笔结果、月度、顺序分组、尾部与币种集中。
本轮不是盲测，也不是新样本；所有差值只作描述性估计。

## 问题（Owner 原文要点）

相对原 V9，加入「下降趋势线突破 + SPIKE」后：筛选收益、等待确认的代价、持仓机会变化分别是什么？
现有提升是否受实现差异或统计口径影响？只做 1h、只做多；不调参、不加过滤、不改出场、不建 V10.5、不 push。

## 原实验（定位结果）

- 路径：`experiments/active/exp-spike-v10-4-joint-multitf-20260918-v1/`；主跑 `results/run_v1`
  （source_commit ccc52dff29，tree 59d8c5139a，run_identity 804d7a41…），敏感性 `results/run_ties_v1`（9e98a0068e）。
- 代码：`yoyo/evaluation/spike_v10_4.py`（移植）、`spike_v10_4_study.py`（runner）、`spike_v10_4_report.py`、
  `spike_v10_4_1h_detail.py`；V9 引擎 `spike_burst_replay.features` / `spike_v7_fast` / `spike_v1_v8_be05.replay_fixed_entry`。
- Pine：`yoyo/evaluation/pine/spike_burst_v10_4_owner.pine`（Owner 原文，本轮不改）；V9 Pine `spike_burst_v9.pine`。
- 自 ccc52dff29 以来移植只新增 `pivots()`/`pivot_ties` 开关（严格路径被测试钉在旧实现上）与本轮的 trace 钩子（只记录）。

## 冻结参数

沿用原 config/manifest：Owner Pine 默认值（`V104Params()`，联合窗口 6 根），V9 引擎不变，
出场 = `replay_fixed_entry`（次根开盘、5 根低点−0.2ATR 与收盘−2ATR 取低、收盘到 2R 起 4ATR 追踪、原始 V9 空头确认次根开盘平仓、0.2% 往返、断档删失），
每个 币×周期 最多一笔。币池 = 原 638 个 Binance 永续；数据 = 原 5m 档案，1h 由同源 5m 按 UTC 整点聚合，预热 1500 根。
信号收盘窗 [2024-09-10, 2026-05-01)，前后段分界 2025-09-10。**读取边界**：任何 5m 行开盘 ≥ 2026-05-01 即报错（不补 5 月 1–3 日），
≥ 2026-05-04 为 holdout。期末未平仓记 censored_boundary、断档记 censored_gap，均不计入已实现盈亏。

## 步骤

1. **复现**：`git archive ccc52dff29` 导出到临时目录（只读，不 checkout、不建 worktree），用原代码只跑 1h，
   与原逐流结果和当前代码逐笔比对（整数/字符串精确，浮点 rtol=atol=1e-10，沿用既有 parity 标准）。
2. **审计**：trace 钩子记录线的出生/突破/结束、SPIKE 证据保存与丢弃原因、配对尝试、每根的屏幕主线；
   逐项核对入场/止损冻结/追踪生效时序/反向退出；数据断档差异按受影响窗口逐事件列出；拐点口径复用原两种。
3. **三臂**（同数据同出场同成本、各自独立持仓）：A `v9_only` = 原 v9_long；B `break_only` = 同一引擎的单独突破事件
   （无 V9/BB/六线/动量/母区间门）；C `joint` = 原联合事件。B 只作模块消融参照。
4. **事件账本**：V9 最终多头、结构、突破、联合各有稳定 ID；联合未成立原因、交易层原因、各臂状态数量守恒。
5. **归因**：4.1 V9 事件影子路径（按是否后来配对分组，事后描述，非可执行过滤）；4.2 等待代价（V9 次根 vs 联合次根，净 bp + 各自 R）；
   4.3 持仓占用（因持仓跳过、C 可交易而 A 持仓中）。
6. **统计**：主量 C−A 每笔净 R 差；UTC 事件月块共同重抽样（种子 91509，2,000 次，95% 区间）；随机对照沿用原方法（种子 91918）作辅助；
   月块符号翻转 p 只用于「相对随机」。
7. **勘误**：「去掉最好 1%（15 笔）剩 1,462」与配对超额分母；导出被删 ID。
8. **核对图**：固定种子 91509，三种顺序各 2 个联合 + 6 个未联合 V9 事件；「当时可见」与「后续路径」分开画。
9. **账户**：复用 `spike_account_growth.simulate_shared_account` 与其既有固定配置，只对 A/C 做已实现余额账本；无逐时浮动盈亏 → 标明未完成。

## 不做

不调任何阈值、不筛币、不加 BTC 过滤、不改保本/追踪、不建 V10.5、不读 holdout、不 push、不前向常驻、不下单。
顺序/影线/月份/币种分组只做诊断，不据此删组或发布「最优配置」。
