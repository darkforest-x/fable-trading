# 同一信号上入场价更好，不等于策略收益更好

- **问题**：V2.21 固定等勾叉后 2 根 15m 收盘再入场。在同一批勾叉上，post2 的入场价比 post0 差 10–19bp（中位 0.21–0.27R），看上去去掉等待就能拿回这部分利润。
- **死胡同**：拿“共同信号的入场价差”直接当作收益改进，推断 post0 会更好。这个比较只覆盖两边都入场的那部分信号。改确认根数还会改变另外两样东西：一是样本集合，post0 多放进 BTC 39 个、ETH 20 个原本被后续实体检查挡掉的信号；二是风险分母，入场离触价极值更近，止损中位数从 0.53%/0.71% 缩到 0.40%/0.52%，固定 20bp 成本折成 R 后变大。结果 BTC post0 全集净 R 是 −65.9，比 post2 的 −47.1 更差，而且比随机对照还低 27R。ETH 只差 0.9R。三组毛利都约等于 0。
- **有效路径**：只改 `after` 一个变量，三组分别生成各自的同周/同半期/同波动桶随机对照，比较全集净 R、每笔净 bp 和超额。同时用 post2 逐笔复现旧账本作为参照，保证差异只来自确认根数。
- **通用规则**：改入场时机（等待根数、确认条件、提前或延后）时，“同信号价差”只能当诊断，不能当结论。第一步先查这次改动是否改变了样本数、止损宽度和费用折 R，再用全集净 R 加匹配随机对照下结论。
- **牵连**：`yoyo/evaluation/range_v221_postbars.py`，`experiments/active/exp-range-v221-postbars-20261021-v1/`。参照 [break-even-comparisons-need-same-entry-fee-aware-pairs.md](break-even-comparisons-need-same-entry-fee-aware-pairs.md)、[pool-internal-metrics-cannot-see-beta.md](pool-internal-metrics-cannot-see-beta.md)。
