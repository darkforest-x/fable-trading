# 硬密集门会把趋势策略赖以盈利的尾部一起删掉

- **问题**：V12F 的 `net cross >= 0` 会让零交叉候选通过，因此把六线总交叉、压缩、净方向、排序和释放全部改成硬 `AND` 看似更符合“密集启动”。但 V13 在 final-preholdout 只剩 63 笔，其中 60 笔止损，回报 -11.29%。
- **死胡同**：把“形态描述得更完整”等同于“作为硬入场门更好”。绝对带宽、绝对排序一致性会排除尚未完成长周期均线重排的早期趋势；V13 相对 V12F 错过了多笔约 +38.6%、+26.6%、+25.8% 的趋势单，同时没有消除短命假启动。
- **有效路径**：先用动态 replay 比较被保留与被删除的完整持仓路径，而不是从旧交易 CSV 静态删行；再检查收益是否依赖少数多日趋势单。结论是六线密集特征应先作为可解释的判断层特征/排序信息，或只识别一种 setup archetype，不应直接替换所有 V9/V12F 信号路径。没有新鲜前向证据前保留 V12F 为 paper comparator。
- **通用规则**：优化趋势策略时，任何过滤器除了看平均收益和胜率，第一步都要量“被删除的 top-tail 趋势收益”和“保留下来的短期止损率”；若两者同时恶化，停止继续堆阈值，回到多路径候选或判断层架构。
- **牵连**：`yoyo/layers/l2_judgment/pine_dense_start.py`、`scripts/research_pine_eth_15m_dense_start.py`、`experiments/active/exp-pine-eth-15m-dense-start-v1/`；15m、20bp、ATR4/3%、break-even、1% risk、cooldown 与 full-state reversal 均保持不变；repository holdout 未读取。
