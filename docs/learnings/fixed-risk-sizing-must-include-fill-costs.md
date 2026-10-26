# 固定亏损预算定仓要包含两次手续费和止损滑点

- **问题**：确认后入场、针底止损的几何距离已知，Owner希望按同一风险加大仓位。如果直接用预算除以入场到针底距离，普通止损也会超过名义预算。
- **死胡同**：只以几何R定仓，平仓后才追加手续费与滑点；再把加仓后的金额收益与旧等金额回测比较，会同时改变风险和统计权重。把止盈改成周一关键位本身不改变止损宽度，也不改变同风险仓位。
- **有效路径**：先计算预计止损成交价 S_f=S*(1-side*slip)，每单位计划损失 L=side*(E-S_f)+f_entry*E+f_stop*S_f，再令数量=B/L。E已含开仓滑点，不重复加。框架使用实际数量记成交费用，同一笔PnL可线性换算为等金额或等预算口径；不为不同口径重复回放。
- **通用规则**：先明确预算究竟覆盖价格风险还是总成交损失，再比较仓位。跳空、资金费、容量和并发持仓仍不由单笔计划损失保证。目标已越过或剩余空间不足付费时必须开仓前记为不入场，不能回填目标利润。
- **牵连**：`yoyo/evaluation/monday_confirmed_keypoints_engine.py`、`tests/evaluation/test_monday_confirmed_keypoints.py`、`experiments/active/exp-monday-5m-confirmed-keypoints-20261026-v1/config.json`。
