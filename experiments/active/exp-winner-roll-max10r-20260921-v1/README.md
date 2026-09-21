# 100U、最多两次加仓：大于10R赢家的条件历史最大值

范围：旧V9全量中严格`net_r > 10`的367笔多单、209个资产。每笔重新以100U开始。结果属于事后标签，使用未来行情决定最优仓位、加仓和退出，不是实时策略。

```bash
.venv/bin/python -m pytest tests/evaluation/test_winner_roll_max10r.py tests/evaluation/test_winner_roll_max10r_study.py -q
# Builder/config/plan/tests must already match committed HEAD.
.venv/bin/python -m yoyo.evaluation.winner_roll_max10r_study \
  --output experiments/active/exp-winner-roll-max10r-20260921-v1/run_v1
```

重现时换一个尚不存在的输出目录；不覆盖旧run。依赖仓内已冻结行情和原始V9统计产物，不自动下载数据。

- `run_v1/trades.csv`：每笔的0/1/2次加仓最优结果。
- `run_v1/per_asset.csv`：按两次加仓最优值选出各资产的一笔，同时保留该笔0/1次结果。
- `run_v1/legs.csv`：每个候选最优方案实际选中的初仓及加仓。
- `run_v1/failures.csv`：无法完整计算的输入或约束失败。
- `run_v1/summary.json`：样本覆盖、配置及来源SHA。
- `run_v1/streams/`：逐流结果与完成收据。

模型用40倍开仓上限、5%平坦维持保证金、额外0.1%清算费预留、0.01U缓冲、每次开平0.1%手续费，零资金费、连续数量。逐根成交价low作为mark代理；无完整历史分档与订单深度。金额是这些假设下的数学最大值，真实交易所是否始终不爆仓未知。

原策略退出根不纳入优化区间；所选事后退出根的整个low仍必须存活，再按该根high结算。允许0、1或2次更高开盘价加仓；不允许摊低成本、平仓后重新开仓或外部追加本金。
