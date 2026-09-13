# V8 退出重播必须只注入入场许可，不能过滤原始反向退出流

- **问题**：V8 的 <=3 ATR 条件只限制新入场；若把它直接应用到原始信号表，已持仓时被 V8 拒绝的反向 V6 确认也会消失，改变了被冻结的退出合同。
- **死胡同**：按旧 V7 trade 表过滤或重写 `signals` 看似能得到 V8 子集，但前者无法产生提前退出后的重入，后者会悄悄删掉反向平仓。
- **有效路径**：保留缓存中的原始 `signals`，只以 `dataclasses.replace` 覆盖 `bb.prior_squeeze_run3` 为 V8 mask；底层重播仍从 raw feed 安排 opposite next-open exit。
- **通用规则**：当过滤器只定义 admission 时，先确认执行器把 entry permission 和 exit feed 分开读取；只有前者能被替换，并用同根 early-exit/opposite、gap/SL priority 测试锁住顺序。
- **牵连**：`yoyo/evaluation/spike_v8_early_exit_study.py`、`tests/evaluation/test_spike_v8_early_exit_study.py`、`exp-spike-v8-early-exit-20260913-v1`；不改变生产执行器或 V8 admission 定义。
