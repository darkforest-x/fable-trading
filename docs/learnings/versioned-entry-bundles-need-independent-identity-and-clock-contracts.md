# 升级过滤版本时，版本身份和入场时钟都必须显式保留

- **问题**：Owner 要求把三个已有候选直接合并为 SPIKE V9。它复用 V8 的串行引擎，但引擎仍输出原来的 cohort/policy 名；日历过滤用确认收盘对应的计划开仓时刻，不能混同于缺口后的真实下一根开盘。
- **死胡同**：只改文件名会让输出继续呈现旧版本身份；用未来下一根时间回写信号准入会引入未来可见性。两者均在复核阶段排除，没有据此跑行情。直接重命名已有 V7/V8 shadow 还会污染冻结历史及其源哈希合同。
- **有效路径**：新建 V9 源文件和明确的 strategy_version/arm；原引擎字段保留为来源。准入仅按信号收盘时已知的计划时钟判断；实际开盘发现缺K时，通过原 gap handler 取消 pending entry，不改过去准入。合成案例让周六计划开仓后的下一根推迟到周日，验证过去信号不变且无延期成交。
- **通用规则**：复用旧引擎不等于复用旧版本身份；信号时间、计划成交时间、实际成交时间必须分别定义。新增准入门不能提前使用未来开盘，也不能截断原始反向退出流。
- **牵连**：`yoyo/evaluation/spike_v9.py`、`yoyo/evaluation/pine/spike_burst_v9.pine`、`tests/evaluation/test_spike_v9.py`；旧 V8 源码与 V7/V8 shadow 冻结。参见 [准入注入与原始反向流](v8-exit-replay-must-inject-admission-without-filtering-reverse-feed.md)。
