# 退出规则的改善必须同时扣除被提前洗出的赢家

- **问题**：Owner 提供 SPIKE V1 三规则退出的 XLSX 近似改善 +4326R；逐 bar、下根生效的回放只改善 +363.075R。为什么回救亏损很多，净改善却很小？
- **死胡同**：只在最终亏损组估算保本可以救回多少，或把 MAE、保本、锁利三个独立改善相加，隐含允许规则事后区分赢家和输家。未拿到该 XLSX 公式，不能断言它实际采用了上述算法，也不能把近似值称为数学上界。
- **有效路径**：冻结原版已平仓的同一组 6170 事件，逐 bar 回放后同时记账。单独 0.5R 保本对原非盈利单改善 +2375.812R，却对原盈利单损害 −2167.573R，同事件净改善仅 +208.239R，875 笔原盈利变成扣费后非盈利。三规则对原非盈利改善 +2901.384R、对原盈利损害 −2528.256R，配对改善 +373.128R；另 67 笔原版未完成交易新退出合计 −10.052R，所以全部闭合交易总差为 +363.075R。
- **通用规则**：比较退出策略先固定事件和初始风险；同时报 rescued losers、clipped winners、同事件闭合 delta 与新增闭合项。censored 市值不能混入兑现收益。所有保护只在存活完整 bar 之后生效，不能回填到已走过的更好价格。
- **牵连**：`experiments/active/exp-spike-v1-triple-exit-20260914-v1/results/stats/original6253_baseline_triple_closed_delta_summary.csv`；`yoyo/evaluation/spike_v1_triple_exit.py`、`spike_v1_triple_stats.py`、`spike_v1_triple_report.py`。本轮历史已被研究过，不构成新盲测；未修改生产规则。
