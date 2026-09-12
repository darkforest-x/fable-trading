# Array-backed replay must prove state-machine parity

- **问题**：V7 的两年回放要对 3,531 个冻结流重复运行 V6 信号与八个执行臂。参考实现在逐 bar 状态机中频繁经过 pandas `iterrows`、`.iloc` 和 Series 切片，单个冻结 Binance 0G 30m 流的 V6 信号需 2.411 秒。
- **死胡同**：只替换成交回放不足以改变主要边界，因为 V6 的 legacy provenance 和结构门同样逐 bar 读取 DataFrame；只消除 `.iloc` 也不够，八个臂会各自让原 ledger 再遍历全部 bars。若把状态机改成向量化规则，又会丢失缺口重置、证据锁存、同 bar 消费和反向退出顺序。
- **有效路径**：保留状态转移顺序和稀疏成交字典，预先把 OHLC、指标、gap、admission 转成 NumPy 数组；ledger 用 `flatnonzero` 只遍历事件。BB 诊断按段保留 BB200/RSI6 输出，但只计算一次先前压缩 run。每次实现记录参考源 SHA，并以合成反手/止损/缺口/尾端样本和一个只读冻结流逐字段比对 ledger 与 trades。冻结 0G 流上信号输出精确一致且为 10.59×，BB 诊断完整表精确一致且为 12.82×，八臂 ledger/trades 精确一致且从 2.053 秒降至 0.112 秒（18.34×；同机单次热路径计时）。
- **通用规则**：先确定性能瓶颈是否包含上游状态机；优化状态机时，逐字段 parity 比聚合收益更严格，且必须将参考源身份与输出等价证明一起冻结。
- **牵连**：`yoyo/evaluation/spike_v7_fast.py`、`tests/evaluation/test_spike_v7_fast.py`、`yoyo/evaluation/spike_v6_wvf_study.py`、`yoyo/evaluation/spike_burst_v6_structure.py`、冻结输入 `experiments/active/exp-spike-v1-twoyear-allmarkets-20260911-v1/data/normalized/binance/0GUSDT_30m.csv.gz`。
