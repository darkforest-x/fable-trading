# 五分钟截止时钟不能经浮点小时往返

- **问题**：K2 后延迟5分钟再入场，仍需精确保留原母信号+72h截止；原包装只接受整数小时等待。
- **死胡同**：将剩余整数分钟除60再传 `pd.Timedelta(hours=...)` 看似等价，但 pandas 2.3.3 的97种0..480分钟等待里32种少1ns，既会打破五分钟网格检查，也不能拿放松容差来替代真实时钟契约。
- **有效路径**：延迟始终从两个UTC时间戳作差，在整数分钟域计算 `4320-delay_minutes`。执行器增加可选 `max_minutes` 整数五分钟倍数，明确优先于继承的小时配置；未提供时旧小时路径原样保留。穷举全部97个等待时点，并逐笔验证最终截止仍等于母时刻+72h。
- **通用规则**：离散事件系统应保留整数基本时间单位，不能先转非精确浮点单位，再通过放松校验掩盖误差。
- **牵连**：`yoyo/evaluation/hourly_impulse_aligned_execution.py`、`yoyo/layers/l3_backtest/hourly_impulse.py`、`tests/test_hourly_impulse_aligned_execution.py`；V6只修精度，不延长等待或持仓上限。依据 [pandas 2.3.3 Timedelta](https://pandas.pydata.org/pandas-docs/version/2.3.3/reference/api/pandas.Timedelta.html)。
