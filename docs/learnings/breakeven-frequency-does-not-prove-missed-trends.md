# 保本单数量多，不能证明保本规则截断了趋势

- **问题**：Owner询问ETH4h R1如何优化。固定S5连续账本69笔中37笔毛收益约+0.1%，全部在0.1%单边手续费下净亏，容易把它归因为收益不足的主要原因。
- **死胡同**：只看保本单数量就建议关闭保本，或认为把偏移调到覆盖费用即可大幅增收。37笔已实现净亏合计仅3.2933602006USDT，相对500USDT初始权益约0.66%；这只是已发生费用拖累，不是改变止损后的可实现收益。不能从已退出交易推断继续持有一定更赚。
- **有效路径**：先从已保存账本拆分退出类型，保持float_precision=round_trip。确认主要待检验的是机会成本与尾部风险：保本退出之后可能延续趋势，也可能触及原始止损。建议后续在Owner授权改变出场规则后预注册保本开关的单变量完整状态回放，并保留相同初始止损、仓位、入场和成本，比较配对对照、分时段表现及尾部回撤。此次没有修改或评估新参数。
- **通用规则**：将直接费用拖累与假设继续持仓的机会成本分开；原账本的退出分组是事后描述，不能当作新策略胜率或因果效果。
- **牵连**：`experiments/active/exp-eth4h-trend-candidate-20260907-v1/results/continuous_S5_close_only_trades.csv`；R1保本触发1.5%、偏移0.1%、单边手续费0.1%；项目障碍与成本修改须Owner明确决定。

复核命令（仅读取冻结账本，不运行新实验）：

```bash
.venv/bin/python - <<'PY'
import pandas as pd
p = 'experiments/active/exp-eth4h-trend-candidate-20260907-v1/results/continuous_S5_close_only_trades.csv'
t = pd.read_csv(p, float_precision='round_trip')
m = (t.gross_return - .001).abs() < 1e-8
print(len(t), int(m.sum()), float(t.loc[m, 'net_pnl'].sum()))
PY
```
