# 账本自洽不等于它来自本轮冻结的行情

- **问题**：统一风控趋势基线复用旧 V9/V9.1 账本作逐笔对照，检查了原始行情 SHA 和旧账本自哈希，却没有把逐币收据绑定到已验证的旧构建身份。
- **死胡同**：增加收益字段 parity 仍不能证明账本属于哪份输入。收据与账本成对替换后可以继续自洽；若交易结果恰好相同，parity 也无法发现来源被替换。
- **有效路径**：先断言逐币收据的 identity_hash、symbol、source_sha256 与冻结身份一致，再验证账本哈希和逐笔结果。用替换三个字段的合成反例确认拒绝。修复先提交，保留 run_v1，再在 run_v2 重跑。
- **通用规则**：跨实验复用产物时，同时验证内容、自身收据和父输入身份三层；单个文件哈希不能代替身份关系。
- **牵连**：`yoyo/evaluation/trend_baseline_study.py`、`tests/evaluation/test_trend_baseline_study.py`、`experiments/active/exp-trend-baselines-20260920-v1/`。不改变交易规则、风险或成本。
