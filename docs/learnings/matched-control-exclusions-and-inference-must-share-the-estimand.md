# 匹配对照的排除集和显著性必须对应同一估计量

- **问题**：V1/V7 在同一币、周期和连续段上分别匹配时，一个版本的真实信号 bar 可能被另一个版本抽成随机控制；同一控制还可能重复配给多个目标。逐笔翻符号又把同月重叠行情误当成独立样本，p 值会过小。
- **死胡同**：只排除当前 variant 的 target、允许控制有放回，再对所有 pair 的差值独立 sign flip。简单改成月均值翻符号也不完整，因为报告的 paired delta 是事件等权，月等权检验可能连方向都不同。
- **有效路径**：同一 stream identity 的 V1/V7 全量 target 时间组成共享排除集；每个 variant 内控制无放回。先按日历月汇总差值的和，再以月为聚类单位翻符号；总 pair 数在置换中固定，因此检验与报告的事件等权平均差只相差一个正常数。
- **通用规则**：匹配前先冻结所有真实事件的联合禁入集，并明确控制是否无放回；聚类检验的聚合权重必须与报告 effect 的权重一致。
- **牵连**：`yoyo/evaluation/spike_market_breadth_matched_controls.py`、`tests/evaluation/test_spike_market_breadth_matched_controls.py`；匹配层为同 venue/symbol/timeframe/segment/month/side/前120根 ATR-close quartile。
