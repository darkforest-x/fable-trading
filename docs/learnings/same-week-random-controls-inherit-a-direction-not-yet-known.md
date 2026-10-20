# 同周随机对照继承信号方向时，信号前的随机点用了未来才知道的方向

- **问题**：区间翻身 BTC/ETH/PEPE 多轮研究一直报告"信号比匹配随机少亏"，被当作入场有优势、只差出场。但信号本身固定持有15m–3天的方向收益都≈0（5m版3天 BTC −4.2/ETH −27.6/PEPE +4.3bp，同期绝对波动中位 220–550bp）。
- **死胡同**：同币×同周×同方向×同波动桶随机，看起来满足 CLAUDE.md 的匹配对照要求。但约一半随机点落在信号之前，那时翻身尚未发生、方向不可知；这些点等于在破位前/破位中按"翻身方向"入场，3天前向 −78/−140/−192bp，逐笔毛 −7.2/−11.2/−13.4bp。信号之后的随机点 3天 −7.5/+3.1/+2.8bp、逐笔毛 +0.2/−0.0/−3.7bp，与信号（+1.3/+2.6/+1.3bp）几乎相同。"优于随机"几乎全部来自对照组的前视偏差。
- **有效路径**：不跑新回测，直接用已有 baseline trades.csv：①按固定持有期算信号方向收益的绝对值；②把随机对照按入场在信号前/后拆开。两步十几秒，就能判定入场没有方向信息，出场优化无从提取利润。
- **通用规则**：随机对照的方向、资格和时间都只能来自对照时点之前已发生的事件；同周匹配只取信号之后（或用不依赖信号的方向）。任何出场/过滤研究开始前，先看信号固定持有期绝对方向收益是否 ≥ 成本的数倍；≈0 就停止调出场。这是 [random-control-exclusion-can-condition-on-future-signals](random-control-exclusion-can-condition-on-future-signals.md) 与 [weak-entry-markouts-cannot-be-blamed-only-on-exits](weak-entry-markouts-cannot-be-blamed-only-on-exits.md) 的重犯——旧笔记存在但未被执行。
- **牵连**：`experiments/active/exp-range-entry5m-20261020-v1/*/results/baseline/trades.csv`、`exp-range-multicoin-20261020-v1/*/results/baseline/trades.csv`（random 的 `pair_id` 与 signal 一一对应，side 100% 相同）。受影响：10-20 全部区间翻身/ETH 5m 研究中的"相对随机"结论。未修改历史结果、配置或成本。
