# 可执行风险与已实现统计必须使用同一交易时钟

- **问题**：SPIKE V1 两年覆盖账本在下一根开盘入场，却用信号收盘的参考风险计算 R；汇总还把期末未平仓行混入已实现胜率、PF 和净收益，并按文件写入顺序计算“最大回撤”。
- **死胡同**：只保存 `signal.risk` 看起来能复现 Pine 图上的风险文字，但它不能代表跳空后的可执行入场风险；把 censored 行视作普通交易会把仍未知的未来收益写进已实现统计。
- **有效路径**：在实际 `entry_price` 和冻结的 `initial_stop` 上计算风险，非正距离保留空 R；汇总先排除 censored 行，再按 `exit_time`、`entry_time`、`event_id` 稳定排序，以零基线的等权事件累加序列记录回撤。
- **通用规则**：每个经济指标先写清楚它所属的时钟和状态。信号参考、可执行成交和观察期截断不能混用；没有仓位重叠和资金分配模型时，不得把事件序列回撤称为账户最大回撤。
- **牵连**：`yoyo/evaluation/spike_v1_twoyear_allmarkets.py`、`tests/test_spike_v1_twoyear_accounting.py`；覆盖重建只读取已冻结 CSV，并在 receipt 中钉住方法版本、Pine 和 replay 源 SHA。
